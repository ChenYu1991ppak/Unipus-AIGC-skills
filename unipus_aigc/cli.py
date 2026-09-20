# -*- coding: utf-8 -*-
"""命令行入口::

    python -m unipus_aigc.cli token             # 检查 token 有效期
    python -m unipus_aigc.cli translate-text "Hello world"
    python -m unipus_aigc.cli translate-doc ./a.docx --to zh --out ./a.zh.docx
    python -m unipus_aigc.cli review essay.txt --level 0 --json
    python -m unipus_aigc.cli kb-ask "暗号是什么？" --kb KBxxxx
    python -m unipus_aigc.cli kb-ask "讲了什么？" --doc ./a.txt --cleanup
    python -m unipus_aigc.cli records           # 列历史记录
    python -m unipus_aigc.cli cleanup --dry-run # 清理测试数据
"""

import argparse
import json
import sys

from . import config
from .client import UnipusAIGC
from .errors import AigcError
from .review import ReviewAPI


def _client(args):
    return UnipusAIGC(timeout=getattr(args, "timeout", 60),
                      verbose=getattr(args, "verbose", False))


def cmd_token(args):
    valid, msg = config.check_token()
    print(("OK  " if valid else "EXPIRED  ") + msg)
    return 0 if valid else 1


def cmd_translate_text(args):
    with _client(args) as cli:
        result = cli.translate.text(args.text, args.from_lang, args.to_lang,
                                    timeout=args.wait)
        text = cli.translate.translated_text(result)
        print(text if text else json.dumps(result, ensure_ascii=False, indent=2))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_translate_doc(args):
    with _client(args) as cli:
        result = cli.translate.document(args.path, args.from_lang, args.to_lang,
                                        name=args.name, timeout=args.wait)
        url = result.get("translateUrl")
        print(f"记录 id : {result.get('id')}")
        print(f"译文地址: {url}")
        if args.out and url:
            cli.download(url, args.out)
            print(f"已保存至: {args.out}")
    return 0


def cmd_review(args):
    text = open(args.path, encoding="utf-8").read() if args.path else args.text
    if not text:
        print("需要 --path 或 --text", file=sys.stderr)
        return 2
    with _client(args) as cli:
        result = cli.review.essay(text, topic=args.topic, level=args.level,
                                  timeout=args.wait)
        if args.json:
            print(ReviewAPI.dump(result))
        else:
            print(ReviewAPI.format_report(result))
    return 0


def cmd_kb_ask(args):
    with _client(args) as cli:
        kb_id = args.kb
        if not kb_id:
            if not args.doc:
                print("需要 --kb 或 --doc", file=sys.stderr)
                return 2
            result = cli.kb.temporary_qa(args.question, args.doc,
                                         cleanup=not args.keep,
                                         timeout=args.wait)
        else:
            result = cli.kb.ask(kb_id, args.question, timeout=args.wait)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(cli.kb.answer_text(result))
    return 0


def cmd_records(args):
    with _client(args) as cli:
        out = {}
        j = cli.translate.records(include_text=False)
        out["翻译"] = [{"id": d.get("id"), "name": d.get("name"),
                        "type": d.get("type"), "created": d.get("created")}
                       for d in ((j or {}).get("data") or [])]
        j = cli.review.records()
        out["智能评阅"] = [{"wmId": d.get("wmId"), "title": d.get("title"),
                            "created": d.get("created")}
                           for d in ((j or {}).get("data") or [])]
        j = cli.kb.list()
        out["知识库"] = [{"kbId": d.get("kbId"), "kbName": d.get("kbName"),
                          "fileCount": d.get("fileCount")}
                         for d in ((j or {}).get("data") or [])]
        for k, v in out.items():
            print(f"===== {k} ({len(v)}) =====")
            for item in v:
                print("  " + json.dumps(item, ensure_ascii=False))
    return 0


def cmd_cleanup(args):
    """删除测试产生的数据。

    默认只列出来（dry-run），加 ``--yes`` 才真删。
    用 ``--ids`` 精确指定要删的记录比 ``--pattern`` 模糊匹配安全。
    """
    with _client(args) as cli:
        by_id = set(args.ids or [])
        targets = {"translate": [], "review": [], "kb": []}

        def keep(ident, name):
            if by_id:
                return ident in by_id
            return not args.pattern or args.pattern in (name or "")

        j = cli.translate.records(include_text=False) or {}
        for d in j.get("data") or []:
            if keep(d.get("id"), d.get("name")):
                targets["translate"].append((d.get("id"), d.get("name")))

        j = cli.review.records() or {}
        for d in j.get("data") or []:
            if keep(d.get("wmId"), d.get("title")):
                targets["review"].append((d.get("wmId"), d.get("title")))

        j = cli.kb.list() or {}
        for d in j.get("data") or []:
            if (d.get("source") or "") == "default":      # 平台自带的默认知识库
                continue
            if keep(d.get("kbId"), d.get("kbName")):
                targets["kb"].append((d.get("kbId"), d.get("kbName")))

        total = sum(len(v) for v in targets.values())
        print(f"匹配到 {total} 条：")
        for kind, items in targets.items():
            for ident, name in items:
                print(f"  [{kind}] {ident}  {name}")

        if args.dry_run or not args.yes:
            print("\n（dry-run，未删除。加 --yes 执行删除）")
            return 0

        for rid, _ in targets["translate"]:
            print(f"translate/delete {rid} ->", cli.translate.delete(rid))
        for wm, _ in targets["review"]:
            print(f"wm/delete {wm} ->", cli.review.delete(wm))
        for kb, _ in targets["kb"]:
            for doc in ((cli.kb.documents(kb) or {}).get("docInfoList") or []):
                print(f"deleteFile {doc.get('docName')} ->",
                      cli.kb.delete_document(kb, doc.get("docId")))
            print(f"project/del {kb} ->", cli.kb.delete(kb))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="unipus-aigc",
                                description="Unipus AIGC 平台 Python 客户端")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 WebSocket 推送")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("token", help="检查 token 是否有效")
    s.set_defaults(func=cmd_token)

    s = sub.add_parser("translate-text", help="文本翻译")
    s.add_argument("text")
    s.add_argument("--from", dest="from_lang", default="en")
    s.add_argument("--to", dest="to_lang", default="zh")
    s.add_argument("--wait", type=int, default=120, help="轮询超时（秒）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_translate_text)

    s = sub.add_parser("translate-doc", help="文档翻译")
    s.add_argument("path")
    s.add_argument("--from", dest="from_lang", default="en")
    s.add_argument("--to", dest="to_lang", default="zh")
    s.add_argument("--name", default=None)
    s.add_argument("--out", default=None, help="把译文保存到本地")
    s.add_argument("--wait", type=int, default=900)
    s.set_defaults(func=cmd_translate_doc)

    s = sub.add_parser("review", help="智能评阅（作文）")
    s.add_argument("--path", default=None, help="作文文件路径")
    s.add_argument("--text", default=None, help="直接给正文")
    s.add_argument("--topic", default="")
    s.add_argument("--level", type=int, default=0, help="0大学 1高中 2初中 3小学")
    s.add_argument("--wait", type=int, default=300)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("kb-ask", help="知识库问答")
    s.add_argument("question")
    s.add_argument("--kb", default=None, help="已有知识库 id")
    s.add_argument("--doc", action="append", default=None,
                   help="临时知识库的文档，可重复；用完即删")
    s.add_argument("--keep", action="store_true", help="保留临时知识库")
    s.add_argument("--wait", type=int, default=300)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_kb_ask)

    s = sub.add_parser("records", help="列出历史记录")
    s.set_defaults(func=cmd_records)

    s = sub.add_parser("cleanup", help="清理测试数据")
    s.add_argument("--ids", action="append", default=None,
                   help="只删这些 id（翻译记录 id / wmId / kbId），可重复")
    s.add_argument("--pattern", default=None, help="只匹配名称里含该字串的记录")
    s.add_argument("--dry-run", action="store_true", default=False,
                   help="只列出，不删除（不带 --yes 时默认也是这个行为）")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_cleanup)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AigcError as e:
        print(f"接口错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
