# -*- coding: utf-8 -*-
"""命令行入口。

子命令按 skill 边界分组（guide / translate / review / kb / kbv2 / speech / oral / sync）::

    # ---- guide 域：凭证与记录管理 ----
    unipus-aigc token                        # 检查 token 有效期
    unipus-aigc set-token --stdin            # 写入凭证（Q23：用户告知 -> skill 落盘）
    unipus-aigc records                      # 列历史记录
    unipus-aigc cleanup --dry-run            # 清理测试数据（默认只列不删）

    # ---- translate ----
    unipus-aigc translate text "Hello world"
    unipus-aigc translate submit-doc ./a.pdf      # 秒级返回记录 id
    unipus-aigc translate poll <recordId>         # 短轮询，可反复调用
    unipus-aigc translate get <recordId>          # 只查一次

    # ---- review ----
    unipus-aigc review essay --path essay.txt --level 0   # 一次性（评阅较快）
    unipus-aigc review submit --path essay.txt --level 0  # 只提交
    unipus-aigc review poll <taskId>
    unipus-aigc review get <taskId>

    # ---- kb ----
    unipus-aigc kb list
    unipus-aigc kb ask "暗号是什么？" --kb KBxxxx
    unipus-aigc kb ask "讲了什么？" --doc ./a.txt        # 临时库，用完即删
    unipus-aigc kb create <name> / upload <kbId> <path> / wait <kbId>
    unipus-aigc kb delete <kbId> --yes

    # ---- kbv2（RAG v2：会话式、分块级溯源）----
    unipus-aigc kbv2 projects --source 1715   # 库列表（必须带 source）
    unipus-aigc kbv2 sources                  # 已实测的 source 取值
    unipus-aigc kbv2 create <name> / upload <kbId> <path> / wait <kbId> / docs <kbId>
    unipus-aigc kbv2 new-session <kbId>
    unipus-aigc kbv2 ask "问题" --kb <kbId> [--session <id>] [--keep]
    unipus-aigc kbv2 approve <qaId> [--value 1|2] / feedback <qaId> "文字"
    unipus-aigc kbv2 delete <kbId> / rm <kbId> <fileId> / clear-history <sessionId>

    # ---- speech ----
    unipus-aigc speech speakers              # 可用音色（实测白名单）
    unipus-aigc speech say "要念的文字" --speaker zh_youyou --out a.mp3
    unipus-aigc speech submit <text>         # 只提交，秒级返回 taskId
    unipus-aigc speech poll <taskId>
    unipus-aigc speech records

    # ---- oral：口语评阅（op90，type="3"）----
    unipus-aigc oral review ./a.mp3 --content "I went to school."
    unipus-aigc oral submit ./a.mp3 --content "..."   # 只提交，返回 wmId+taskId
    unipus-aigc oral poll <taskId>
    unipus-aigc oral detail <wmId>                    # 记录详情（这条链路有结果）
    unipus-aigc oral records / oral delete <wmId>     # **cleanup 看不见这些记录**

    # ---- sync：同步 operation，结果就在提交响应里 ----
    unipus-aigc sync ops                     # 已实测的同步 operation 清单
    unipus-aigc sync standards               # 课标列表（op11 的 courseStandardId 来源）
    unipus-aigc sync cs-qa "问题" --standard-id 0     # op 11 课标问答
    unipus-aigc sync prompt-optimize "画只猫"          # op 14 提示词优化
    unipus-aigc sync prompt-translate "画只猫"         # op 15（实测回空 content）
    unipus-aigc sync kb-view                 # op 17 知识库文档列表
    unipus-aigc sync question-answer <rmId> "题干"      # op 13（实测回 status=9）
    unipus-aigc sync submit --operation N --data '<json>'   # 通用入口

实际调用方式是经过各 skill 的壳脚本::

    bash skills/<app>/scripts/run.sh <子命令> [参数...]

退出码::

    0  成功
    1  真失败（接口错误 / 任务失败 / 缺凭证）
    2  用法错误（argparse）
    3  **仍在处理中**——这不是错误。状态存在平台侧，稍后用同一个 id 再 poll 一次。

长任务为什么必须拆两步：Bash 工具上限 600s，而文档翻译实测要几分钟到十几分钟，
一次前台阻塞调用必然超时、拿不到结果。拆成 submit（秒级返回 id）+ poll（短轮询、
可反复调用）之后，中断也能重入——状态本来就记在平台上（``translate/list``、
``wm/list``），不需要我们自己存。
"""

import argparse
import json
import os
import re
import sys
import time

from . import config
from .client import UnipusAIGC
from .errors import AigcError, MissingTokenError, StillRunning, TaskFailed, TaskTimeout
from .rag_v2 import KB_SOURCES, RagSession
from .constants import Operation
from .oral_review import DEFAULT_QUES_TYPE, OralReviewAPI
from .review import ReviewAPI
from .speech import SpeechAPI
from .sync_ops import SyncAPI
from .trans_review import SUPPORTED_LANGS, TransReviewAPI
from .translate import TranslateAPI

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_STILL_RUNNING = 3


# ----------------------------------------------------------------------
# 公共辅助
# ----------------------------------------------------------------------
def _client(args):
    return UnipusAIGC(timeout=getattr(args, "timeout", 60),
                      verbose=getattr(args, "verbose", False))


def _note(msg):
    """进度/提示类信息走 stderr，保持 stdout 只有结果，便于管道与解析。"""
    print(msg, file=sys.stderr)


def _cache_path(kind, ident):
    d = config.cache_dir()
    if not d:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(ident))
    return os.path.join(d, f"{kind}-{safe}.json")


def _save_result(kind, ident, data):
    """把原始 JSON 落到 ``~/.cache/unipus-aigc/<kind>-<id>.json``，返回路径。"""
    path = _cache_path(kind, ident)
    if not path:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        _note(f"[警告] 结果缓存写入失败：{e}")
        return None
    return path


def _dump(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def _translate_summary(result, record_id=None):
    """``translate/detail`` 的可读摘要。"""
    lines = [f"记录 id : {result.get('id') or record_id or ''}"]
    url = result.get("translateUrl")
    lines.append(f"译文地址: {url or '(尚未生成)'}")
    lines.append(f"status  : {result.get('status')}   （三态：2=进行中 / 1=成功 / 4=失败）")
    if result.get("msg"):
        lines.append(f"msg     : {result['msg']}")
    flows = result.get("flowResponses") or []
    if flows:
        states = ", ".join(str(f.get("status")) for f in flows)
        lines.append(f"flowResponses.status: {states}   （实测失败时可能是 0，不可靠）")
    lines.append("注：判「完成」要看 translateUrl/translation 是否非空，不能看 status。")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# guide 域
# ----------------------------------------------------------------------
def cmd_token(args):
    valid, msg = config.check_token()
    print(("OK  " if valid else "EXPIRED  ") + msg)
    return EXIT_OK if valid else EXIT_ERROR


def cmd_set_token(args):
    """把用户给的 JWT 落盘（Q23）。**任何情况下都不回显 token 本身。**"""
    if args.stdin:
        token = sys.stdin.read()
    elif args.token:
        token = args.token
    else:
        _note("需要 --stdin（推荐）或直接给 token 位置参数")
        return 2
    token = (token or "").strip()
    if not token:
        _note("读到的是空值，未写入")
        return 2

    r = config.save_token(token, path=args.path, scope=args.scope)
    print(f"已写入 : {r['path']}")
    print(f"指纹   : {r['fingerprint']}")
    if r.get("previous_fingerprint"):
        print(f"旧指纹 : {r['previous_fingerprint']}")
    if r.get("backup"):
        print(f"旧凭证已备份: {r['backup']}")
    print(f"状态   : {r['message']}")
    return EXIT_OK if r["valid"] else EXIT_ERROR


def cmd_records(args):
    with _client(args) as cli:
        out = {}
        # includeText 是记录类型的开关：False 只回文档翻译，True 只回文本翻译，
        # 两个列表互斥，必须都查一遍才看得全（详见 translate.records 的说明）。
        for label, it in (("翻译", False), ("翻译(文本)", True)):
            j = cli.translate.records(include_text=it)
            out[label] = [{"id": d.get("id"), "name": d.get("name"),
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
    return EXIT_OK


def cmd_cleanup(args):
    """删除测试产生的数据。

    默认只列出来（dry-run），加 ``--yes`` 才真删。
    用 ``--ids`` 精确指定要删的记录比 ``--pattern`` 模糊匹配安全。

    **这个安全默认是硬约束**：任何调用方（包括 skill）都不得代替用户按 ``--yes``。
    """
    with _client(args) as cli:
        by_id = set(str(i) for i in (args.ids or []))
        targets = {"translate": [], "review": [], "kb": [], "speech": []}

        def keep(ident, name):
            if by_id:
                return str(ident) in by_id
            return not args.pattern or args.pattern in (name or "")

        # includeText 是记录类型的开关（False=文档翻译 / True=文本翻译），
        # 两种记录互斥，**两个都要扫**——只扫默认那个会把文本翻译的残留漏掉。
        for include_text in (False, True):
            j = cli.translate.records(include_text=include_text) or {}
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

        j = cli.speech.records(size=100) or {}
        for d in j.get("data") or []:
            if keep(d.get("id"), d.get("text")):
                targets["speech"].append((d.get("id"), (d.get("text") or "")[:40]))

        total = sum(len(v) for v in targets.values())
        print(f"匹配到 {total} 条：")
        for kind, items in targets.items():
            for ident, name in items:
                print(f"  [{kind}] {ident}  {name}")

        if args.dry_run or not args.yes:
            print("\n（dry-run，未删除。加 --yes 执行删除）")
            return EXIT_OK

        for rid, _ in targets["translate"]:
            print(f"translate/delete {rid} ->", cli.translate.delete(rid))
        for wm, _ in targets["review"]:
            print(f"wm/delete {wm} ->", cli.review.delete(wm))
        for kb, _ in targets["kb"]:
            for doc in ((cli.kb.documents(kb) or {}).get("docInfoList") or []):
                print(f"deleteFile {doc.get('docName')} ->",
                      cli.kb.delete_document(kb, doc.get("docId")))
            print(f"project/del {kb} ->", cli.kb.delete(kb))
        if targets["speech"]:
            ids = [ident for ident, _ in targets["speech"]]
            print(f"speech/delUrl {ids} ->", cli.speech.delete(*ids))
    return EXIT_OK


# ----------------------------------------------------------------------
# translate
# ----------------------------------------------------------------------
def cmd_translate_text(args):
    with _client(args) as cli:
        result = cli.translate.text(args.text, args.from_lang, args.to_lang,
                                    timeout=args.wait)
        text = cli.translate.translated_text(result)
        print(text if text else _dump(result))
        if args.json:
            print(_dump(result))
        p = _save_result("translate", result.get("id") or "text", result)
        if p:
            _note(f"[原始 JSON] {p}")
        if args.out and result.get("translateUrl"):
            cli.download(result["translateUrl"], args.out)
            print(f"已保存至: {args.out}")
    return EXIT_OK


def cmd_translate_submit_doc(args):
    with _client(args) as cli:
        rec = cli.translate.submit_document(args.path, args.from_lang,
                                           args.to_lang, name=args.name)
    rid = rec.get("id")
    print(f"记录 id : {rid}")
    print(f"名称    : {rec.get('name')}")
    print(f"语种    : {rec.get('languageFrom')} -> {rec.get('languageTo')}")
    _note("")
    _note("已提交。文档翻译通常要几分钟到十几分钟，用下面这条查进度（可反复调用）：")
    _note(f"  run.sh translate poll {rid}")
    return EXIT_OK


def cmd_translate_poll(args):
    with _client(args) as cli:
        result = cli.translate.poll(args.id, timeout=args.wait)
        print(_translate_summary(result, args.id))
        text = cli.translate.translated_text(result)
        if text:
            print("--- 译文 ---")
            print(text)
        if args.out and result.get("translateUrl"):
            cli.download(result["translateUrl"], args.out)
            print(f"已保存至: {args.out}")
        if args.json:
            print(_dump(result))
        p = _save_result("translate", args.id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_translate_get(args):
    """只查一次，不轮询。没出结果 -> 退出码 3；已失败 -> 退出码 1。"""
    with _client(args) as cli:
        result = cli.translate.detail(args.id) or {}
    reason = TranslateAPI.failed_reason(result)
    if reason:
        raise TaskFailed(f"翻译任务 {args.id} 失败：{reason}",
                         path="translate/detail", payload=result)
    if not (result.get("translation") or result.get("translateUrl")):
        _note(f"记录 {args.id} 还没有结果，仍在处理中。稍后再查一次。")
        return EXIT_STILL_RUNNING
    print(_translate_summary(result, args.id))
    if args.json:
        print(_dump(result))
    return EXIT_OK


# ----------------------------------------------------------------------
# review
# ----------------------------------------------------------------------
def _review_text(args):
    if args.path:
        with open(args.path, encoding="utf-8") as fh:
            return fh.read()
    return args.text


def cmd_review_submit(args):
    text = _review_text(args)
    if not text:
        _note("需要 --path 或 --text")
        return 2
    with _client(args) as cli:
        sub = cli.review.submit_essay(text, topic=args.topic, level=args.level,
                                      title=args.title)
    print(f"wmId   : {sub['wmId']}")
    print(f"taskId : {sub['taskId']}")
    print(f"标题   : {sub['title']}")
    _note("")
    _note("已提交。查结果（用 taskId，**不是** wmId）：")
    _note(f"  run.sh review poll {sub['taskId']}")
    return EXIT_OK


def cmd_review_poll(args):
    with _client(args) as cli:
        result = cli.review.poll(args.task_id, timeout=args.wait)
        print(ReviewAPI.dump(result) if args.json else ReviewAPI.format_report(result))
        p = _save_result("review", args.task_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_review_get(args):
    with _client(args) as cli:
        result = cli.review.get(args.task_id)
    if result is None:
        _note(f"任务 {args.task_id} 仍在处理中。稍后再查一次。")
        return EXIT_STILL_RUNNING
    print(ReviewAPI.dump(result) if args.json else ReviewAPI.format_report(result))
    return EXIT_OK


def cmd_review_essay(args):
    """一次性评阅（评阅通常几十秒内出结果，不会撞 600s 上限）。"""
    text = _review_text(args)
    if not text:
        _note("需要 --path 或 --text")
        return 2
    with _client(args) as cli:
        result = cli.review.essay(text, topic=args.topic, level=args.level,
                                  title=args.title, timeout=args.wait)
        print(ReviewAPI.dump(result) if args.json else ReviewAPI.format_report(result))
        p = _save_result("review", result.get("taskId") or "essay", result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


# ----------------------------------------------------------------------
# kb
# ----------------------------------------------------------------------
def cmd_kb_list(args):
    with _client(args) as cli:
        j = cli.kb.list() or {}
    for d in j.get("data") or []:
        print(json.dumps({"kbId": d.get("kbId"), "kbName": d.get("kbName"),
                          "fileCount": d.get("fileCount"),
                          "source": d.get("source")}, ensure_ascii=False))
    return EXIT_OK


def cmd_kb_create(args):
    with _client(args) as cli:
        kb_id = cli.kb.create(args.name, desc=args.desc)
    print(kb_id)
    return EXIT_OK


def cmd_kb_upload(args):
    """**只提交，不等解析**。解析状态用 ``kb wait`` 查。"""
    doc_name = args.name or os.path.basename(args.path)
    with _client(args) as cli:
        info = cli.kb.upload_document(args.kb_id, args.path, doc_name, wait=False)
    print(f"kbId    : {info['kbId']}")
    print(f"docName : {info['docName']}")
    print(f"status  : {info['status']}")
    _note("")
    _note("已提交解析。查状态（必须等 status=green 且 chunkSize>0 才能提问）：")
    _note(f"  run.sh kb wait {info['kbId']} --doc {info['docName']}")
    return EXIT_OK


def cmd_kb_wait(args):
    with _client(args) as cli:
        doc = cli.kb.poll_document(args.kb_id, args.doc, timeout=args.wait)
    print(f"docName  : {doc.get('docName')}")
    print(f"status   : {doc.get('status')}")
    print(f"chunkSize: {doc.get('chunkSize')}")
    if doc.get("summary"):
        print(f"summary  : {doc['summary']}")
    _note("解析完成，可以提问了。")
    return EXIT_OK


def cmd_kb_ask(args):
    with _client(args) as cli:
        kb_id = args.kb
        temp = False
        if not kb_id:
            if not args.doc:
                _note("需要 --kb 或 --doc")
                return 2
            kb_id = cli.kb.create(
                args.name or f"临时知识库-{time.strftime('%Y%m%d-%H%M%S')}")
            temp = True
            _note(f"已建临时知识库: {kb_id}")
            for path in args.doc:
                cli.kb.upload_document(kb_id, path, wait=False)

            # 等解析。超时时**保留**知识库并退出 3——这样可以用 --kb 续跑，
            # 而不是把已上传的文档连同库一起删掉、白干一场。
            try:
                for path in args.doc:
                    cli.kb.poll_document(kb_id, os.path.basename(path),
                                         timeout=args.doc_wait)
            except StillRunning as e:
                _note(str(e))
                _note(f"知识库已保留：{kb_id}")
                _note("文档解析完成后，用这两条续跑：")
                _note(f"  run.sh kb wait {kb_id} --doc <文件名>")
                _note(f"  run.sh kb ask \"<问题>\" --kb {kb_id}")
                # 续跑提示里不带 --yes：这条命令默认只列不删，
                # 删不删由用户确认（见 CLAUDE.md 的硬约束）。
                _note(f"不再需要时清理（只列不删）：run.sh kb delete {kb_id}")
                return EXIT_STILL_RUNNING

        try:
            result = cli.kb.ask(kb_id, args.question, timeout=args.wait)
        except StillRunning as e:
            _note(str(e))
            if temp:
                _note(f"知识库已保留：{kb_id}")
                _note(f"稍后续跑：run.sh kb ask \"<问题>\" --kb {kb_id}")
                _note(f"不再需要时清理（只列不删）：run.sh kb delete {kb_id}")
            return EXIT_STILL_RUNNING

        if temp and not args.keep:
            cli.kb.delete(kb_id)
            _note(f"[已清理临时知识库 {kb_id}]")

        print(_dump(result) if args.json else cli.kb.answer_text(result))
        src = result.get("sourceName") if isinstance(result, dict) else None
        if src:
            _note(f"[溯源] {src}  {result.get('sourceUrl') or ''}")
        p = _save_result("kb", result.get("taskId") if isinstance(result, dict) else "ask",
                         result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_kb_submit_question(args):
    with _client(args) as cli:
        task_id = cli.kb.submit_question(args.kb_id, args.question)
    print(f"taskId : {task_id}")
    _note("")
    _note("已提交。查结果：")
    _note(f"  run.sh kb poll {task_id}")
    return EXIT_OK


def cmd_kb_poll(args):
    with _client(args) as cli:
        result = cli.kb.poll(args.task_id, timeout=args.wait)
        print(_dump(result) if args.json else cli.kb.answer_text(result))
        p = _save_result("kb", args.task_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_kb_delete(args):
    """删知识库。**不带 --yes 只列不删**，与 cleanup 同一条安全底线。"""
    if not args.yes:
        print("将删除以下知识库（dry-run，未删除。加 --yes 执行删除）：")
        for kb in args.kb_ids:
            print(f"  {kb}")
        return EXIT_OK
    with _client(args) as cli:
        for kb in args.kb_ids:
            for doc in ((cli.kb.documents(kb) or {}).get("docInfoList") or []):
                print(f"deleteFile {doc.get('docName')} ->",
                      cli.kb.delete_document(kb, doc.get("docId")))
            print(f"project/del {kb} ->", cli.kb.delete(kb))
    return EXIT_OK


# ----------------------------------------------------------------------
# speech
# ----------------------------------------------------------------------
def cmd_speech_speakers(args):
    print(SpeechAPI.speaker_table())
    _note("")
    _note("这是**实测**白名单，不是抄文档——文档里音色表被压成一列，名参对应不上。")
    _note("拼错的音色接口会回 HTTP 500，但**记录行照样建**，所以这里先拦住。")
    return EXIT_OK


def cmd_speech_submit(args):
    with _client(args) as cli:
        sub = cli.speech.submit(args.text, args.speaker, type_=args.type,
                                language=args.language, speed=args.speed,
                                volume=args.volume, audio_type=args.audio_type,
                                sub_type=args.subtype)
    print(f"taskId   : {sub['taskId']}")
    print(f"音色     : {sub['speaker']}")
    print(f"语言     : {sub['language']}   （1 中文 / 2 英文）")
    print(f"subType  : {sub['subType']}   （4 语音合成 / 38 听力音频）")
    _note("")
    _note("已提交。合成通常几秒，直接查结果：")
    _note(f"  run.sh speech poll {sub['taskId']}")
    return EXIT_OK


def cmd_speech_poll(args):
    with _client(args) as cli:
        result = cli.speech.poll(args.task_id, timeout=args.wait)
        print(SpeechAPI.format_result(result))
        if args.out and result.get("audioUrl"):
            cli.download(result["audioUrl"], args.out)
            print(f"已保存至 : {args.out}")
        if args.json:
            print(_dump(result))
        p = _save_result("speech", args.task_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_speech_say(args):
    """提交 + 等待一次做完。合成是秒级任务，不需要拆两步。"""
    with _client(args) as cli:
        result = cli.speech.say(args.text, args.speaker, type_=args.type,
                                language=args.language, speed=args.speed,
                                volume=args.volume, audio_type=args.audio_type,
                                sub_type=args.subtype, timeout=args.wait)
        print(SpeechAPI.format_result(result))
        if args.out and result.get("audioUrl"):
            cli.download(result["audioUrl"], args.out)
            print(f"已保存至 : {args.out}")
        if args.json:
            print(_dump(result))
        p = _save_result("speech", result.get("id") or result.get("taskId"), result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_speech_get(args):
    """只查一次，不轮询。没出结果 -> 退出码 3；已失败 -> 退出码 1。"""
    with _client(args) as cli:
        result = cli.speech.get(args.task_id)
    if result is None:
        _note(f"任务 {args.task_id} 还没有结果，仍在处理中。稍后再查一次。")
        return EXIT_STILL_RUNNING
    print(SpeechAPI.format_result(result))
    if args.json:
        print(_dump(result))
    return EXIT_OK


def cmd_speech_records(args):
    with _client(args) as cli:
        page = cli.speech.records(page=args.page, size=args.size,
                                  sub_type=args.subtype) or {}
    rows = page.get("data") or []
    print(f"共 {page.get('totalCount')} 条，本页 {len(rows)} 条")
    for d in rows:
        print(json.dumps({"id": d.get("id"), "speaker": d.get("speaker"),
                          "status": d.get("status"), "audioUrl": d.get("audioUrl"),
                          "created": d.get("created"),
                          "text": (d.get("text") or "")[:40]}, ensure_ascii=False))
    if rows:
        _note("")
        _note("注：这里的 status 只有 4 个取值（1 已提交/2 执行中/3 成功/4 失败），")
        _note("跟 queryTask 的 7 态不是同一套。删记录用上面的 id（不是 taskId）。")
    return EXIT_OK


# ----------------------------------------------------------------------
# oral —— 口语评阅（operation 90）
# ----------------------------------------------------------------------
def cmd_oral_submit(args):
    with _client(args) as cli:
        sub = cli.oral.submit(args.audio, args.evaluation_content,
                              title=args.title, content=args.content or "",
                              language_type=args.language_type,
                              ques_type=args.ques_type, grade_id=args.grade_id)
    print(f"wmId       : {sub['wmId']}")
    print(f"taskId     : {sub['taskId']}")
    print(f"音频地址   : {sub['audioFileUrl']}")
    if args.content:
        print(f"朗读原文   : {args.content}")
    _note("")
    _note("已提交。查结果（用 taskId）：")
    _note(f"  run.sh oral poll {sub['taskId']}")
    _note(f"清理记录（**口语评阅不在 cleanup 的扫描范围内**，要用 oral delete）：")
    _note(f"  run.sh oral delete {sub['wmId']}")
    return EXIT_OK


def cmd_oral_review(args):
    """一次性评阅（提交 + 等待）。口语评阅通常几秒到几十秒出结果。"""
    if not (args.evaluation_content or args.content):
        _note("需要 --evaluation-content 或 --content：实测 evaluationContent 是必填。")
        return 2
    with _client(args) as cli:
        result = cli.oral.review(args.audio, args.evaluation_content,
                                 title=args.title, content=args.content or "",
                                 language_type=args.language_type,
                                 ques_type=args.ques_type, grade_id=args.grade_id,
                                 timeout=args.wait)
        print(OralReviewAPI.dump(result) if args.json
              else OralReviewAPI.format_report(result))
        p = _save_result("oral", result.get("taskId") or "review", result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_oral_poll(args):
    with _client(args) as cli:
        result = cli.oral.poll(args.task_id, timeout=args.wait)
        print(OralReviewAPI.dump(result) if args.json
              else OralReviewAPI.format_report(result))
        p = _save_result("oral", args.task_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_oral_get(args):
    """只查一次，不轮询。没出结果 -> 退出码 3。"""
    with _client(args) as cli:
        result = cli.oral.get(args.task_id)
    if result is None:
        _note(f"任务 {args.task_id} 还没有结果，仍在处理中。稍后再查一次。")
        return EXIT_STILL_RUNNING
    print(OralReviewAPI.dump(result) if args.json
          else OralReviewAPI.format_report(result))
    return EXIT_OK


def cmd_oral_detail(args):
    """记录详情。**跟作文评阅不同，这里的 evaluationList 是填好的。**"""
    with _client(args) as cli:
        d = cli.oral.detail(args.wm_id) or {}
    rows = d.get("evaluationList") or []
    if not rows:
        print(_dump(d) if args.json else "(这条记录还没有评阅结果)")
        return EXIT_OK
    for row in rows:
        print(OralReviewAPI.dump(row) if args.json
              else OralReviewAPI.format_report(row))
        _note(f"[evaluationStatus] {row.get('evaluationStatus')}  "
              f"（1/2 评阅中，3 成功；4/8/9 不出结果。**跟 queryTask 的 7 态不是同一套**）")
    _note("")
    _note("注：这条链路的 wm/detail **是有结果的**（跟作文评阅相反）。")
    _note("但顶层 evaluation 仍为 null，内容在 evaluationList[0].evaluation 里。")
    return EXIT_OK


def cmd_oral_records(args):
    with _client(args) as cli:
        page = cli.oral.records(page=args.page, size=args.size,
                                type_=args.type) or {}
    rows = page.get("data") or []
    print(f"共 {page.get('totalCount')} 条，本页 {len(rows)} 条")
    for d in rows:
        print(json.dumps({"wmId": d.get("wmId"), "title": d.get("title"),
                          "type": d.get("type"), "wordCount": d.get("wordCount"),
                          "gradeId": d.get("gradeId"),
                          "created": d.get("created")}, ensure_ascii=False))
    _note("")
    _note("注：`cleanup` 只扫 type=\"1\"（作文评阅），**看不见这里的记录**——")
    _note("清理要显式用 `oral delete <wmId>`。")
    return EXIT_OK


def cmd_oral_delete(args):
    """删口语评阅记录。**不带 --yes 只列不删**，与 cleanup / kb delete 同一条底线。"""
    if not args.yes:
        print("将删除以下记录（dry-run，未删除。加 --yes 执行删除）：")
        for wm in args.wm_ids:
            print(f"  {wm}")
        return EXIT_OK
    with _client(args) as cli:
        for wm in args.wm_ids:
            print(f"wm/delete {wm} ->", cli.oral.delete(wm))
    return EXIT_OK


# ----------------------------------------------------------------------
# trans-review —— 翻译评阅（operation 36）
#
# **它不是"译后编辑"**：只回一个分数，不产出改后的译文。早期文档把它记错了，
# 详见 lib/unipus_aigc/trans_review.py 的模块 docstring 与 docs/call-chains.md §8。
# ----------------------------------------------------------------------
def cmd_trans_review_submit(args):
    src, tgt = _trans_review_texts(args)
    if src is None:
        return 2
    try:
        with _client(args) as cli:
            sub = cli.trans_review.submit(src, tgt, src_lang=args.src_lang,
                                          tgt_lang=args.tgt_lang, title=args.title,
                                          wm_id=args.wm_id)
    except ValueError as e:
        return _tr_input_error(e)
    print(f"wmId   : {sub['wmId']}")
    print(f"taskId : {sub['taskId']}")
    print(f"语言对 : {sub['srcLang']} -> {sub['tgtLang']}")
    _note("")
    _note("已提交。查结果（用 taskId）：")
    _note(f"  run.sh trans-review poll {sub['taskId']}")
    _note("清理记录（**翻译评阅不在 cleanup 的扫描范围内**，要用 trans-review delete）：")
    _note(f"  run.sh trans-review delete {sub['wmId']}")
    return EXIT_OK


def cmd_trans_review_review(args):
    """一次性评阅（提交 + 等待）。实测几秒出结果。"""
    src, tgt = _trans_review_texts(args)
    if src is None:
        return 2
    try:
        with _client(args) as cli:
            result = cli.trans_review.review(src, tgt, src_lang=args.src_lang,
                                             tgt_lang=args.tgt_lang,
                                             title=args.title, wm_id=args.wm_id,
                                             timeout=args.wait)
    except ValueError as e:
        return _tr_input_error(e)
    print(TransReviewAPI.dump(result) if args.json
          else TransReviewAPI.format_report(result))
    p = _save_result("trans-review", result.get("taskId") or "review", result)
    if p:
        _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_trans_review_poll(args):
    with _client(args) as cli:
        result = cli.trans_review.poll(args.task_id, timeout=args.wait)
        print(TransReviewAPI.dump(result) if args.json
              else TransReviewAPI.format_report(result))
        p = _save_result("trans-review", args.task_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_trans_review_get(args):
    """只查一次，不轮询。没出结果 -> 退出码 3。"""
    with _client(args) as cli:
        result = cli.trans_review.get(args.task_id)
    if result is None:
        _note(f"任务 {args.task_id} 还没有结果，仍在处理中。稍后再查一次。")
        return EXIT_STILL_RUNNING
    print(TransReviewAPI.dump(result) if args.json
          else TransReviewAPI.format_report(result))
    return EXIT_OK


def cmd_trans_review_detail(args):
    """记录详情。**这一档的 evaluation 在顶层且填好了**。"""
    with _client(args) as cli:
        d = cli.trans_review.detail(args.wm_id) or {}
    if args.json:
        print(_dump(d))
    else:
        print(f"记录 wmId: {d.get('wmId')}")
        print(f"标题     : {d.get('title')}")
        print(f"类型     : {d.get('type')}  （2 = 翻译评阅）")
        print(f"语言对   : {d.get('langFrom')} -> {d.get('langTo')}")
        print(f"题目原文 : {d.get('content')}")
        print(f"待评译文 : {d.get('translation')}")
        print(f"创建时间 : {d.get('created')}")
        ev = d.get("evaluation")
        if isinstance(ev, dict):
            print(f"评阅得分 : {ev.get('score')}   （uuid={ev.get('uuid')}）")
        else:
            print("评阅得分 : (这条记录还没有评阅结果)")
        print(f"isEvaluation: {d.get('isEvaluation')}")
    _note("")
    _note("注：这里的 `translation` 是**提交时那份待评译文的原样存档**，")
    _note("    不是平台改过的译文——op36 不产出译文。")
    return EXIT_OK


def cmd_trans_review_records(args):
    with _client(args) as cli:
        page = cli.trans_review.records(page=args.page, size=args.size,
                                        type_=args.type) or {}
    rows = page.get("data") or []
    print(f"共 {page.get('totalCount')} 条，本页 {len(rows)} 条")
    for d in rows:
        print(json.dumps({"wmId": d.get("wmId"), "title": d.get("title"),
                          "type": d.get("type"), "wordCount": d.get("wordCount"),
                          "created": d.get("created")}, ensure_ascii=False))
    _note("")
    _note("注：`cleanup` 只扫 type=\"1\"（作文评阅），**看不见这里的记录**——")
    _note("清理要显式用 `trans-review delete <wmId>`。")
    return EXIT_OK


def cmd_trans_review_delete(args):
    """删翻译评阅记录。**不带 --yes 只列不删**，与 cleanup / kb delete 同一条底线。"""
    if not args.yes:
        print("将删除以下记录（dry-run，未删除。加 --yes 执行删除）：")
        for wm in args.wm_ids:
            print(f"  {wm}")
        return EXIT_OK
    with _client(args) as cli:
        for wm in args.wm_ids:
            print(f"wm/delete {wm} ->", cli.trans_review.delete(wm))
    return EXIT_OK


def _tr_input_error(exc):
    """翻译评阅的本地前置校验没过。

    这一类是**用法错误**（退出码 2），不是接口错误——``--wm-id`` 跟语种对不上、
    ``wm/create`` 没回 wmId，都属于"给的东西不对"，不是"平台坏了"。
    更要紧的是：这一档平台**就是不报错**，只会静默给个假分数，
    所以必须在这里挡住，并且说清为什么。
    """
    _note(f"输入不合法，没有提交到平台：{exc}")
    return 2


def _trans_review_texts(args):
    """取题目原文与待评译文。返回 ``(src, tgt)``；失败返回 ``(None, None)``。

    刻意**不对正文做 strip 之类的加工**——评阅吃的是原文，
    改动一个字符都可能让分数不是"这份译文"的分数。
    """
    src = args.src_text
    if not src and args.src_file:
        with open(args.src_file, encoding="utf-8") as fh:
            src = fh.read()
    tgt = args.tgt_text
    if not tgt and args.tgt_file:
        with open(args.tgt_file, encoding="utf-8") as fh:
            tgt = fh.read()
    if not src or not tgt:
        missing = []
        if not src:
            missing.append("题目原文（--src-file / --src-text）")
        if not tgt:
            missing.append("待评译文（--tgt-file / --tgt-text）")
        _note("缺少：" + "、".join(missing))
        return None, None
    return src, tgt


# ----------------------------------------------------------------------
# sync —— 同步 operation（11 / 13 / 14 / 15 / 17）
# ----------------------------------------------------------------------
def _sync_outcome(outcome, label):
    """同步 operation 的统一收口：三档分开处理，**不把 9 当成功也不当失败**。

    返回 ``(退出码, outcome)``。0 有结果、3 语义未知或还在跑，
    **没有 1**——这一档里出现的失败都是 ``AigcError``，由 ``main()`` 兜成 1。
    """
    if outcome.done:
        _note(f"[{label}] operation={outcome.operation} status={outcome.status} "
              f"taskId={outcome.task_id}")
        return EXIT_OK, outcome
    if outcome.note:
        # status=9：文档 7 态枚举之外。**不当成功也不当失败**，退出码 3。
        _note(f"[{label}] {outcome.note}")
        _note(f"[原始 value] {_dump(outcome.raw)}")
        return EXIT_STILL_RUNNING, outcome
    _note(f"[{label}] operation={outcome.operation} status={outcome.status} "
          f"（在跑）taskId={outcome.task_id}")
    _note("这不是同步 operation 的预期形态。拿 taskId 去 poll：")
    _note(f"  run.sh sync poll {outcome.task_id}")
    return EXIT_STILL_RUNNING, outcome


def cmd_sync_ops(args):
    print("同步 operation：结果就在 task/submit 的响应里，**没有轮询这一步**。")
    for op in sorted(Operation.SYNC):
        print(f"  {op:<4}{_SYNC_NOTE.get(op, '')}")
    _note("")
    _note("这是**实测**出来的集合（2026-09-20），不是照抄文档的枚举表——")
    _note("真正的判据在 submit_sync 就地看返回的 status，不在这个集合上。")
    return EXIT_OK


_SYNC_NOTE = {
    11: "课标问答      通，返回 content + page",
    13: "智能答题      **实测回 status=9（枚举之外），拿不到结果**",
    14: "提示词优化    通，返回 content（优化后的提示词）",
    15: "中文提示词翻译 通，但 content **实测是空串**，原因未查明",
    17: "知识库查看    通，docInfo 解析后是 [{doc_name, doc_url}]",
}


def cmd_sync_standard_list(args):
    """列课标（``cs/queryList``）——op11 的 ``courseStandardId`` 只能从这里拿。"""
    with _client(args) as cli:
        rows = (cli.get_value("cs/queryList", {}) or {}).get("data") or []
    for d in rows:
        print(json.dumps(d, ensure_ascii=False))
    _note("")
    _note("注：op11 的 courseStandardId **不是可省字段**——实测不传报")
    _note("`courseStandardId字段不能为空`，而只给 id 不给 content 又报")
    _note("`content字段不能为空`，两个都要。返回行里的 0 是合法 id。")
    return EXIT_OK


def cmd_sync_cs_qa(args):
    """op 11 课标问答。"""
    with _client(args) as cli:
        outcome = cli.sync.course_standard_qa(args.question, args.standard_id)
    code, outcome = _sync_outcome(outcome, "课标问答")
    if code == EXIT_OK:
        result = outcome.result or {}
        print(result.get("content") or "(空)")
        _note(f"[page] {result.get('page')}   [source] {result.get('source') or '(空)'}")
        if args.json:
            print(_dump(result))
        p = _save_result("sync-standard-qa", outcome.task_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return code


def cmd_sync_prompt_optimize(args):
    """op 14 提示词优化。"""
    with _client(args) as cli:
        outcome = cli.sync.prompt_optimize(args.text)
    code, outcome = _sync_outcome(outcome, "提示词优化")
    if code == EXIT_OK:
        print(SyncAPI.content_of(outcome) or "(空)")
        if args.json:
            print(_dump(outcome.result))
        p = _save_result("sync-prompt-optimize", outcome.task_id, outcome.result)
        if p:
            _note(f"[原始 JSON] {p}")
    return code


def cmd_sync_prompt_translate(args):
    """op 15 中文提示词翻译。**实测返回空 content，原因未查明**——如实报空。"""
    with _client(args) as cli:
        outcome = cli.sync.prompt_translate(args.text)
    code, outcome = _sync_outcome(outcome, "中文提示词翻译")
    if code == EXIT_OK:
        content = SyncAPI.content_of(outcome)
        if content:
            print(content)
        else:
            print("(空)")
            _note("")
            _note("**这是已知问题，不是用法错误**：接口 status=3、不报错，但 content")
            _note("是空串。原因没查出来，文档里也没有说明，所以不做猜测性的兜底。")
            _note("要英文提示词先用 op14 优化、或走别的通道。")
        if args.json:
            print(_dump(outcome.result))
        p = _save_result("sync-prompt-translate", outcome.task_id, outcome.result)
        if p:
            _note(f"[原始 JSON] {p}")
    return code


def cmd_sync_kb_view(args):
    """op 17 知识库查看。"""
    with _client(args) as cli:
        outcome = cli.sync.kb_view(args.user_id)
    code, outcome = _sync_outcome(outcome, "知识库查看")
    if code == EXIT_OK:
        docs = SyncAPI.docs_of(outcome)
        print(f"共 {len(docs)} 篇")
        for d in docs:
            if isinstance(d, dict):
                print(f"  {d.get('doc_name')}\t{d.get('doc_url')}")
            else:
                # 解不开 JSON 时 docInfo 会原样留着，至少让用户看见原始值。
                print(f"  {d}")
        if args.json:
            print(_dump(outcome.result))
        p = _save_result("sync-kb-view", outcome.task_id, docs)
        if p:
            _note(f"[原始 JSON] {p}")
    return code


def cmd_sync_question_answer(args):
    """op 13 智能答题。**实测回 status=9，本命令存在的意义是把它如实暴露出来。**"""
    with _client(args) as cli:
        outcome = cli.sync.question_answer(args.rm_id, args.question)
    code, outcome = _sync_outcome(outcome, "智能答题")
    _note("")
    _note("注：这一条**没有可用结果**，不是用法问题也不是临时的平台抖动——")
    _note("2026-09-20 实测提交后回 status=9（文档 7 态枚举之外），responseData 是 null。")
    _note("入参按文档给全了（rmId + question），平台不报错、也不出结果。")
    _note("**别把它当成功**，也别当失败——语义没读懂的码不给它定性。")
    return code


def cmd_sync_submit(args):
    """通用入口：直接给 operation + submitData(JSON)。"""
    try:
        data = json.loads(args.data)
    except ValueError as e:
        _note(f"--data 不是合法 JSON：{e}")
        return 2
    with _client(args) as cli:
        outcome = cli.sync.submit(args.operation, data)
    code, outcome = _sync_outcome(outcome, f"op{args.operation}")
    if code == EXIT_OK:
        print(_dump(outcome.result))
        p = _save_result(f"sync-{args.operation}", outcome.task_id, outcome.result)
        if p:
            _note(f"[原始 JSON] {p}")
    return code


def cmd_sync_poll(args):
    """兜底：万一某个"同步"operation 回了 1/2/6（在跑），用这个把结果取回来。

    实测**没有一条同步 operation 走到这里**（它们提交时就直接给结果），所以
    这条路径没有实测证据。真碰上了还有一层不确定：同步任务的 taskId
    ``queryTask`` 认不认，未知——查不到会退出码 3 并把真实 status 带出来。
    """
    with _client(args) as cli:
        result = cli.wait_task(args.task_id, timeout=args.wait)
    print(_dump(result))
    return EXIT_OK



def cmd_kbv2_sources(args):
    print("已实测的 source 取值（`--source` 只收这几个）：")
    for value, desc in KB_SOURCES.items():
        print(f"  {value:<9}{desc}")
    _note("")
    _note("注意：**用哪个 source 建的库，就只能在同一 source 的 projects 里看见**。")
    _note("看不到库时先怀疑这个，不是库丢了。想看全量用老链路的 `kb list`（聚合视图）。")
    return EXIT_OK


def cmd_kbv2_projects(args):
    with _client(args) as cli:
        rows = cli.rag_v2.projects(args.source)
    for d in rows:
        print(json.dumps({"kbId": d.get("kbId"), "kbName": d.get("kbName"),
                          "id": d.get("id"), "source": d.get("source")},
                         ensure_ascii=False))
    _note(f"[{len(rows)} 个库] 注：行里的 source 是建库方式（create），"
          f"不是查询用的 source。")
    return EXIT_OK


def cmd_kbv2_create(args):
    with _client(args) as cli:
        kb_id = cli.rag_v2.create(args.name, source=args.source)
    print(kb_id)
    _note(f"已建（source={args.source}）。后续 projects 要带同一个 --source，否则看不见它。")
    return EXIT_OK


def cmd_kbv2_rename(args):
    with _client(args) as cli:
        print(cli.rag_v2.rename(args.kb_id, args.new_name))
    return EXIT_OK


def cmd_kbv2_delete(args):
    """删库。**不带 --yes 只列不删**，与 cleanup / kb delete 同一条安全底线。"""
    with _client(args) as cli:
        for kb in args.kb_ids:
            docs = cli.rag_v2.doc_rows(kb)
            if not args.yes:
                print(f"将删除知识库 {kb}（含 {len(docs)} 篇文档）：")
                for d in docs:
                    print(f"    {d.get('docName')}  docId={d.get('docId')}")
            else:
                for d in docs:
                    print(f"deleteFile {d.get('docName')} ->",
                          cli.rag_v2.delete_document(kb, d.get("docId")))
                print(f"project/del {kb} ->", cli.rag_v2.delete(kb))
    if not args.yes:
        print("（dry-run，未删除。加 --yes 执行删除——由用户确认后再加。）")
    return EXIT_OK


def cmd_kbv2_upload(args):
    """**只提交，不等解析**。解析状态用 ``kbv2 wait`` 查。"""
    doc_name = args.name or os.path.basename(args.path)
    with _client(args) as cli:
        info = cli.rag_v2.upload_document(args.kb_id, args.path, doc_name,
                                          wait=False, doc_type=args.doc_type)
    print(f"kbId    : {info.get('projectId')}")
    print(f"docName : {info.get('filename')}")
    print(f"url     : {info.get('url')}")
    _note("")
    _note("已提交解析。查状态（必须等 status=green 且 chunkSize>0 才能提问）：")
    _note(f"  run.sh kbv2 wait {info.get('projectId')} --doc {info.get('filename')}")
    return EXIT_OK


def cmd_kbv2_wait(args):
    with _client(args) as cli:
        doc = cli.rag_v2.poll_document(args.kb_id, args.doc, timeout=args.wait)
    print(f"docName  : {doc.get('docName')}")
    print(f"status   : {doc.get('status')}")
    print(f"chunkSize: {doc.get('chunkSize')}")
    if doc.get("summary"):
        print(f"summary  : {doc['summary']}")
    _note("解析完成，可以提问了。")
    return EXIT_OK


def cmd_kbv2_docs(args):
    with _client(args) as cli:
        page = cli.rag_v2.documents(args.kb_id, page=args.page, size=args.size)
    rows = page.get("docInfoList") or []
    print(f"共 {page.get('total')} 篇，本页 {len(rows)} 篇")
    for d in rows:
        print(json.dumps({"docId": d.get("docId"), "docName": d.get("docName"),
                          "status": d.get("status"), "chunkSize": d.get("chunkSize"),
                          "uploadTime": d.get("uploadTime")}, ensure_ascii=False))
    _note("")
    _note("status: gray 未开始 / purple 解析中 / green 完成。")
    _note("**gray 和 purple 的 chunkSize 都是 -1**，判完成要 status=green 且 chunkSize>0。")
    return EXIT_OK


def cmd_kbv2_rm(args):
    """删文档。**不带 --yes 只列不删**——删文档跟删库一样不可逆。"""
    if not args.yes:
        print("将删除以下文档（dry-run，未删除。加 --yes 执行删除）：")
        for fid in args.file_ids:
            print(f"  kb={args.kb_id}  fileId={fid}")
        return EXIT_OK
    with _client(args) as cli:
        for fid in args.file_ids:
            print(f"deleteFile {fid} ->", cli.rag_v2.delete_document(args.kb_id, fid))
    return EXIT_OK


def cmd_kbv2_ask(args):
    """问一轮。**同步接口**，不等轮询。

    没给 ``--session`` 就建一个临时会话，问完删掉（``--keep`` 保留）；
    给了 ``--session`` 就在旧会话里续问——**多轮追问必须用 ``--session``**。
    """
    with _client(args) as cli:
        api = cli.rag_v2
        if args.session:
            sess = RagSession(api, args.kb, session_id=args.session)
            temp = False
        else:
            sess = api.session(args.kb, name=args.name)
            temp = True
            _note(f"已建临时会话: {sess.session_id}")

        try:
            result = sess.ask(args.question, use_history=not args.no_history)
        except AigcError as e:
            _note(f"提问失败: {e}")
            if temp:
                _note(f"会话已保留：{sess.session_id}")
                _note(f"稍后续跑：run.sh kbv2 ask \"<问题>\" --kb {args.kb} "
                      f"--session {sess.session_id}")
                _note(f"不再需要时清理（只列不删）：run.sh kbv2 clear-history "
                      f"{sess.session_id}")
            return EXIT_ERROR

        answer = result.get("answer") or ""
        if temp and not args.keep:
            sess.close()
            _note(f"[已清理临时会话 {sess.session_id}]")
        else:
            _note(f"[会话 {sess.session_id}] 续问：加 --session {sess.session_id}")

        print(_dump(result) if args.json else answer)
        _note(f"[qaId] {result.get('qaId')}  "
              f"（认可：kbv2 approve {result.get('qaId')}；反馈：kbv2 feedback）")
        p = _save_result("kbv2", result.get("qaId") or sess.session_id, result)
        if p:
            _note(f"[原始 JSON] {p}")
    return EXIT_OK


def cmd_kbv2_sessions(args):
    with _client(args) as cli:
        rows = cli.rag_v2.sessions(args.kb_id)
    for d in rows:
        print(json.dumps({"sessionId": d.get("sessionId"),
                          "sessionName": d.get("sessionName"),
                          "kbId": d.get("kbId")}, ensure_ascii=False))
    _note(f"[{len(rows)} 个会话] 这个接口**只回最新的 100 条**，没有分页参数。")
    return EXIT_OK


def cmd_kbv2_session(args):
    with _client(args) as cli:
        print(_dump(cli.rag_v2.session_detail(args.session_id)))
    return EXIT_OK


def cmd_kbv2_new_session(args):
    with _client(args) as cli:
        print(cli.rag_v2.new_session(args.kb_id, args.name))
    return EXIT_OK


def cmd_kbv2_history(args):
    with _client(args) as cli:
        rows = cli.rag_v2.history(args.session_id)
    for d in rows:
        print(json.dumps({"id": d.get("id"), "role": d.get("role"),
                          "approve": d.get("approve"), "created": d.get("created"),
                          "content": (d.get("content") or "")[:80]},
                         ensure_ascii=False))
    _note(f"[{len(rows)} 行] role 是中文（用户/系统）。")
    _note("**同一个 id 会在用户行和系统行上各出现一次**——那是问答对 id，不是行主键，")
    _note("拿它去重会丢掉一半的行。")
    return EXIT_OK


def cmd_kbv2_clear_history(args):
    """清问答记录。**不带 --yes 只列不删**；且默认清空**整个会话**。"""
    if not args.yes:
        _note("qaDel 不给 id 时**删除整个会话下的问答记录**，不是删一条。")
        print(f"将清空会话 {args.session_id} 的**全部**问答记录"
              f"（dry-run，未删除。加 --yes 执行）：")
        with _client(args) as cli:
            for d in cli.rag_v2.history(args.session_id):
                print(f"  id={d.get('id')} role={d.get('role')} "
                      f"{(d.get('content') or '')[:50]}")
        return EXIT_OK
    with _client(args) as cli:
        print("qaDel ->", cli.rag_v2.clear_history(args.session_id, args.qa_id))
    return EXIT_OK


def cmd_kbv2_feedback(args):
    with _client(args) as cli:
        print("user/feedback ->", cli.rag_v2.feedback(args.qa_id, args.content))
    return EXIT_OK


def cmd_kbv2_approve(args):
    with _client(args) as cli:
        print("answer/approve ->", cli.rag_v2.approve(args.qa_id, args.approve))
    return EXIT_OK


def cmd_kbv2_prompt(args):
    """取 / 设 / 删库级提示词。三者互斥，默认取。"""
    with _client(args) as cli:
        api = cli.rag_v2
        if args.delete:
            print("del/prompt ->", api.delete_prompt(args.kb_id))
        elif args.set is not None:
            print("prompt ->", api.set_prompt(args.kb_id, args.set))
        else:
            value = api.get_prompt(args.kb_id)
            if value.get("prompt") is None:
                print("(没有提示词)")
                _note("注：提示词不存在时接口返回 kbId/prompt 都是 null，**不报错**。")
            else:
                print(value.get("prompt"))
    return EXIT_OK


# ----------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------
def _add_wait(s, default, help="轮询上限（秒）。到点未完成则退出码 3，不是错误"):
    s.add_argument("--wait", type=int, default=default, help=help)


def build_parser():
    p = argparse.ArgumentParser(
        prog="unipus-aigc",
        description="Unipus AIGC 平台客户端（经 skill 壳脚本 run.sh 调用）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 WebSocket 推送")
    sub = p.add_subparsers(dest="cmd", required=True)

    # ---- guide 域 ----
    s = sub.add_parser("token", help="检查 token 是否有效")
    s.set_defaults(func=cmd_token)

    s = sub.add_parser("set-token", help="把 JWT 写入落盘（不回显 token）")
    s.add_argument("token", nargs="?", default=None, help="JWT；推荐改用 --stdin")
    s.add_argument("--stdin", action="store_true",
                   help="从标准输入读 JWT（避免出现在 ps 里）")
    s.add_argument("--scope", choices=["user", "cwd"], default="user",
                   help="user=~/.config/unipus-aigc/.env（默认）；cwd=./.env")
    s.add_argument("--path", default=None, help="显式路径，给了就忽略 --scope")
    s.set_defaults(func=cmd_set_token)

    s = sub.add_parser("records", help="列出历史记录")
    s.set_defaults(func=cmd_records)

    s = sub.add_parser("cleanup", help="清理测试数据（默认只列不删）")
    s.add_argument("--ids", action="append", default=None,
                   help="只删这些 id（翻译记录 id / wmId / kbId），可重复")
    s.add_argument("--pattern", default=None, help="只匹配名称里含该字串的记录")
    s.add_argument("--dry-run", action="store_true", default=False,
                   help="只列出，不删除（不带 --yes 时默认也是这个行为）")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_cleanup)

    # ---- translate ----
    tp = sub.add_parser("translate", help="翻译（文本 / 文档）")
    tsub = tp.add_subparsers(dest="subcmd", required=True)

    s = tsub.add_parser("text", help="文本翻译（一次性，通常几秒）")
    s.add_argument("text")
    s.add_argument("--from", dest="from_lang", default="en")
    s.add_argument("--to", dest="to_lang", default="zh")
    _add_wait(s, 120)
    s.add_argument("--out", default=None, help="若有译文文件则保存到本地")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_translate_text)

    s = tsub.add_parser("submit-doc", help="提交文档翻译，秒级返回记录 id")
    s.add_argument("path")
    s.add_argument("--from", dest="from_lang", default="en")
    s.add_argument("--to", dest="to_lang", default="zh")
    s.add_argument("--name", default=None)
    s.set_defaults(func=cmd_translate_submit_doc)

    s = tsub.add_parser("poll", help="短轮询文档翻译结果")
    s.add_argument("id")
    _add_wait(s, 60)
    s.add_argument("--out", default=None, help="把译文文件保存到本地")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_translate_poll)

    s = tsub.add_parser("get", help="只查一次，不轮询")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_translate_get)

    # ---- review ----
    rp = sub.add_parser("review", help="智能评阅（作文）")
    rsub = rp.add_subparsers(dest="subcmd", required=True)

    for name, help_text, func in (
            ("essay", "一次性评阅（评阅较快）", cmd_review_essay),
            ("submit", "只提交，秒级返回 taskId", cmd_review_submit)):
        s = rsub.add_parser(name, help=help_text)
        s.add_argument("--path", default=None, help="作文文件路径")
        s.add_argument("--text", default=None, help="直接给正文")
        s.add_argument("--topic", default="")
        s.add_argument("--level", type=int, default=0, help="0大学 1高中 2初中 3小学")
        s.add_argument("--title", default=None)
        if name == "essay":
            _add_wait(s, 300)
            s.add_argument("--json", action="store_true")
        s.set_defaults(func=func)

    s = rsub.add_parser("poll", help="短轮询评阅结果")
    s.add_argument("task_id", metavar="taskId")
    _add_wait(s, 60)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_review_poll)

    s = rsub.add_parser("get", help="只查一次，不轮询")
    s.add_argument("task_id", metavar="taskId")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_review_get)

    # ---- kb ----
    kp = sub.add_parser("kb", help="知识库问答（RAG）")
    ksub = kp.add_subparsers(dest="subcmd", required=True)

    s = ksub.add_parser("list", help="列出我的知识库")
    s.set_defaults(func=cmd_kb_list)

    s = ksub.add_parser("create", help="新建知识库，打印 kbId")
    s.add_argument("name")
    s.add_argument("--desc", default="")
    s.set_defaults(func=cmd_kb_create)

    s = ksub.add_parser("upload", help="上传文档（只提交，不等解析）")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("path")
    s.add_argument("--name", default=None, help="文档名，默认取文件名")
    s.set_defaults(func=cmd_kb_upload)

    s = ksub.add_parser("wait", help="短轮询文档解析状态")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("--doc", default=None, help="文档名；不给则看最新一篇")
    _add_wait(s, 60, help="轮询上限（秒）。到点未解析完则退出码 3")
    s.set_defaults(func=cmd_kb_wait)

    s = ksub.add_parser("ask", help="提问（--kb 已有库 / --doc 临时库）")
    s.add_argument("question")
    s.add_argument("--kb", default=None, help="已有知识库 id")
    s.add_argument("--doc", action="append", default=None,
                   help="临时知识库的文档，可重复；成功答完即删")
    s.add_argument("--name", default=None, help="临时知识库名")
    s.add_argument("--keep", action="store_true", help="保留临时知识库")
    s.add_argument("--doc-wait", type=int, default=240,
                   help="等文档解析的上限（秒）；超时会保留知识库并退出码 3")
    _add_wait(s, 180)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_kb_ask)

    s = ksub.add_parser("submit-question", help="只提交问题，秒级返回 taskId")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("question")
    s.set_defaults(func=cmd_kb_submit_question)

    s = ksub.add_parser("poll", help="短轮询问答结果")
    s.add_argument("task_id", metavar="taskId")
    _add_wait(s, 60)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_kb_poll)

    s = ksub.add_parser("delete", help="删知识库（不带 --yes 只列不删）")
    s.add_argument("kb_ids", nargs="+", metavar="kbId")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_kb_delete)

    # ---- kbv2 —— RAG 框架 v2 ----
    #
    # 跟 `kb` 的分工：老链路 `kb` 是 op102 的一次性问答（单条溯源、无会话），
    # 适合"一个问题、一个临时库"。`kbv2` 是 v2 那一代接口，多出会话、问答历史、
    # 认可/反馈、分块级溯源（source_file_info）——**多轮追问和要溯源时用它**。
    vp = sub.add_parser("kbv2", help="RAG v2（会话 / 追问 / 分块级溯源）")
    vsub = vp.add_subparsers(dest="subcmd", required=True)

    s = vsub.add_parser("sources", help="列出已实测的 source 取值")
    s.set_defaults(func=cmd_kbv2_sources)

    s = vsub.add_parser("projects", help="列知识库（必须带 --source）")
    s.add_argument("--source", default="1715", help="业务来源，见 `kbv2 sources`")
    s.set_defaults(func=cmd_kbv2_projects)

    s = vsub.add_parser("create", help="建知识库，打印 kbId")
    s.add_argument("name")
    s.add_argument("--source", default="1715", help="业务来源，见 `kbv2 sources`")
    s.set_defaults(func=cmd_kbv2_create)

    s = vsub.add_parser("rename", help="重命名知识库")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("new_name", metavar="newName")
    s.set_defaults(func=cmd_kbv2_rename)

    s = vsub.add_parser("delete", help="删知识库（不带 --yes 只列不删）")
    s.add_argument("kb_ids", nargs="+", metavar="kbId")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_kbv2_delete)

    s = vsub.add_parser("upload", help="上传文档（只提交，不等解析）")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("path")
    s.add_argument("--name", default=None,
                   help="文档名，默认取文件名。**后缀必须小写**（v2 的硬要求）")
    s.add_argument("--doc-type", default=None,
                   help="文档类型；不给按扩展名推断（pdf/word/document/…）")
    s.set_defaults(func=cmd_kbv2_upload)

    s = vsub.add_parser("wait", help="短轮询文档解析状态")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("--doc", default=None, help="文档名；不给则看最新一篇")
    _add_wait(s, 60, help="轮询上限（秒）。到点未解析完则退出码 3")
    s.set_defaults(func=cmd_kbv2_wait)

    s = vsub.add_parser("docs", help="列知识库里的文档")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=10)
    s.set_defaults(func=cmd_kbv2_docs)

    s = vsub.add_parser("rm", help="删文档（不带 --yes 只列不删）")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("file_ids", nargs="+", metavar="fileId")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_kbv2_rm)

    s = vsub.add_parser("ask", help="提问（同步，走会话）")
    s.add_argument("question")
    s.add_argument("--kb", required=True, metavar="kbId")
    s.add_argument("--session", default=None,
                   help="已有会话 id；给了就在它里面**续问**（多轮追问用这个）")
    s.add_argument("--name", default=None, help="新会话名")
    s.add_argument("--keep", action="store_true", help="保留临时会话")
    s.add_argument("--no-history", action="store_true",
                   help="不带上下文问（默认会在同一会话里续问）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_kbv2_ask)

    s = vsub.add_parser("sessions", help="列知识库下的会话（只回最新 100 条）")
    s.add_argument("kb_id", metavar="kbId")
    s.set_defaults(func=cmd_kbv2_sessions)

    s = vsub.add_parser("session", help="会话详情")
    s.add_argument("session_id", metavar="sessionId")
    s.set_defaults(func=cmd_kbv2_session)

    s = vsub.add_parser("new-session", help="新建会话，打印 sessionId")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("--name", default=None)
    s.set_defaults(func=cmd_kbv2_new_session)

    s = vsub.add_parser("history", help="问答记录（qaList）")
    s.add_argument("session_id", metavar="sessionId")
    s.set_defaults(func=cmd_kbv2_history)

    s = vsub.add_parser("clear-history",
                        help="清问答记录（不带 --yes 只列不删；默认清空整个会话）")
    s.add_argument("session_id", metavar="sessionId")
    s.add_argument("--qa-id", default=None, type=int,
                   help="只删这一条问答记录；**不给就是清空整个会话**")
    s.add_argument("--yes", action="store_true", help="确认清理")
    s.set_defaults(func=cmd_kbv2_clear_history)

    s = vsub.add_parser("feedback", help="给一条问答记录留反馈")
    s.add_argument("qa_id", metavar="qaId", type=int)
    s.add_argument("content")
    s.set_defaults(func=cmd_kbv2_feedback)

    s = vsub.add_parser("approve", help="认可/不认可一条回答")
    s.add_argument("qa_id", metavar="qaId", type=int)
    s.add_argument("--value", dest="approve", type=int, default=1,
                   help="0 默认 / 1 认可 / 2 不认可")
    s.set_defaults(func=cmd_kbv2_approve)

    s = vsub.add_parser("prompt", help="看/设/删库级提示词（默认只看）")
    s.add_argument("kb_id", metavar="kbId")
    s.add_argument("--set", default=None, metavar="TEXT", help="设置提示词")
    s.add_argument("--del", dest="delete", action="store_true",
                   help="删除提示词")
    s.set_defaults(func=cmd_kbv2_prompt)

    # ---- sync —— 同步 operation（11 / 13 / 14 / 15 / 17）----
    #
    # 跟 translate / review / speech 的分工：那几组的 operation 是**异步**的，
    # submit 拿 taskId、再 poll。这一组的 operation 在 `task/submit` 的响应里
    # 就把 status=3 + responseData 一起给了，**没有 poll 这一步**。
    # 底层判据是 `client.submit_sync` 就地看 status，不是照这张表查。
    yp = sub.add_parser("sync", help="同步 operation（结果在提交响应里）")
    ysub = yp.add_subparsers(dest="subcmd", required=True)

    s = ysub.add_parser("ops", help="列出已实测的同步 operation")
    s.set_defaults(func=cmd_sync_ops)

    s = ysub.add_parser("standards", help="列课标（op11 的 courseStandardId 来源）")
    s.set_defaults(func=cmd_sync_standard_list)

    s = ysub.add_parser("cs-qa", help="op 11 课标问答")
    s.add_argument("question")
    s.add_argument("--standard-id", type=int, default=None,
                   help="课标 id，从 `sync standards` 拿。**实测必填**")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sync_cs_qa)

    s = ysub.add_parser("prompt-optimize", help="op 14 提示词优化")
    s.add_argument("text")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sync_prompt_optimize)

    s = ysub.add_parser("prompt-translate",
                        help="op 15 中文提示词翻译（实测回空 content）")
    s.add_argument("text")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sync_prompt_translate)

    s = ysub.add_parser("kb-view", help="op 17 知识库文档列表")
    s.add_argument("--user-id", default=None, help="不给就用当前账号")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sync_kb_view)

    s = ysub.add_parser("question-answer",
                        help="op 13 智能答题（实测回 status=9，拿不到结果）")
    s.add_argument("rm_id", metavar="rmId", help="阅读材料 id")
    s.add_argument("question")
    s.set_defaults(func=cmd_sync_question_answer)

    s = ysub.add_parser("submit", help="通用入口：直接给 operation + submitData")
    s.add_argument("--operation", type=int, required=True)
    s.add_argument("--data", default="{}", help="submitData，JSON 字符串")
    s.set_defaults(func=cmd_sync_submit)

    s = ysub.add_parser("poll",
                        help="兜底轮询（同步 operation 正常走不到这一步）")
    s.add_argument("task_id", metavar="taskId")
    _add_wait(s, 60)
    s.set_defaults(func=cmd_sync_poll)

    # ---- speech ----
    sp = sub.add_parser("speech", help="语音合成（文字转音频）")
    ssub = sp.add_subparsers(dest="subcmd", required=True)

    s = ssub.add_parser("speakers", help="列出可用的音色（实测白名单）")
    s.set_defaults(func=cmd_speech_speakers)

    def _add_speech_args(p, wait_default=None, wait_help=None):
        p.add_argument("--speaker", default="zh_youyou",
                       help="音色参数名，默认 zh_youyou；跑 `speech speakers` 看全量")
        p.add_argument("--type", type=int, default=1, help="1 单人（默认） / 2 多人")
        p.add_argument("--language", type=int, default=None,
                       help="1 中文 / 2 英文；不给按音色前缀猜")
        p.add_argument("--speed", type=float, default=1.0, help="语速，1.0 为原速")
        p.add_argument("--volume", type=float, default=1.0,
                       help="音量，文档给的区间是 0.9 ~ 1.2")
        p.add_argument("--audio-type", default="mp3", help="音频格式，实测 mp3")
        p.add_argument("--subtype", type=int, default=4,
                       help="4 语音合成（默认） / 38 生成听力音频")
        if wait_default is not None:
            _add_wait(p, wait_default, help=wait_help)

    s = ssub.add_parser("say", help="合成一段文字，一次拿到音频地址")
    s.add_argument("text")
    _add_speech_args(s, 120)
    s.add_argument("--out", default=None, help="把音频保存到本地")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_speech_say)

    s = ssub.add_parser("submit", help="只提交，秒级返回 taskId")
    s.add_argument("text")
    _add_speech_args(s)
    s.set_defaults(func=cmd_speech_submit)

    s = ssub.add_parser("poll", help="短轮询合成结果")
    s.add_argument("task_id", metavar="taskId")
    _add_wait(s, 60)
    s.add_argument("--out", default=None, help="把音频保存到本地")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_speech_poll)

    s = ssub.add_parser("get", help="只查一次，不轮询")
    s.add_argument("task_id", metavar="taskId")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_speech_get)

    s = ssub.add_parser("records", help="列出合成记录")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=20)
    s.add_argument("--subtype", type=int, default=4)
    s.set_defaults(func=cmd_speech_records)

    # ---- oral —— 口语评阅（operation 90，type="3"）----
    #
    # 跟 `review`（作文评阅）的分工：两条链路都走 `wm/*` + `task/submit`，
    # 但**记录类型和 operation 都不同**（作文 type="1"/subType:93/op35，
    # 口语 type="3"/无 subType/op90），而且有两个实质差别：
    #   1. 口语评阅要先把音频传七牛，`audioFileUrl` 是必填；
    #   2. **`wm/detail` 在口语评阅下是有结果的**（作文评阅那边 `evaluation`
    #      恒为 null）。见 oral_review.py 的模块 docstring。
    # 还有一条容易踩空：`cleanup` 只扫 type="1"，**看不见口语评阅的记录**，
    # 清理必须显式走 `oral delete`。
    op = sub.add_parser("oral", help="口语评阅（音频评分 + 发音反馈）")
    osub = op.add_subparsers(dest="subcmd", required=True)

    def _add_oral_args(p, wait_default=None, wait_help=None):
        p.add_argument("--title", default=None,
                       help="记录标题；不给按时间戳生成")
        p.add_argument("--content", default=None,
                       help="朗读原文。**同时充当 evaluationContent 的兜底**")
        p.add_argument("--evaluation-content", default=None, dest="evaluation_content",
                       help="评测内容；不给则退用 --content。实测服务端**必填**")
        p.add_argument("--language-type", type=int, default=None, dest="language_type",
                       help="语言类型（口语评阅自有字段）")
        p.add_argument("--ques-type", type=int, default=None, dest="ques_type",
                       help="题目类型，**实测必填**（不给用默认 %d=朗读短文）；"
                            "要写在 wm/create 上，放 submitData 里没用"
                            % DEFAULT_QUES_TYPE)
        p.add_argument("--grade-id", default=None, dest="grade_id",
                       help="学段 id，从 grade/list 拿（需 type+subType=69）")
        if wait_default is not None:
            _add_wait(p, wait_default, help=wait_help)

    s = osub.add_parser("review", help="一次性评阅：提交 + 等结果（较快）")
    s.add_argument("audio", help="本地音频路径，或七牛上的 URL")
    _add_oral_args(s, 300)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_oral_review)

    s = osub.add_parser("submit", help="只提交，返回 wmId + taskId")
    s.add_argument("audio", help="本地音频路径，或七牛上的 URL")
    _add_oral_args(s)
    s.set_defaults(func=cmd_oral_submit)

    for name, help_text, func in (
            ("poll", "短轮询评阅结果", cmd_oral_poll),
            ("get", "只查一次，不轮询", cmd_oral_get)):
        s = osub.add_parser(name, help=help_text)
        s.add_argument("task_id", metavar="taskId")
        if name == "poll":
            _add_wait(s, 60)
        s.add_argument("--json", action="store_true")
        s.set_defaults(func=func)

    s = osub.add_parser("detail", help="记录详情（**这条链路 wm/detail 有结果**）")
    s.add_argument("wm_id", metavar="wmId")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_oral_detail)

    s = osub.add_parser("records", help="列出评阅记录（type=\"3\"）")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=10)
    s.add_argument("--type", default="3",
                   help="记录类型，默认 3 口语评阅（1 作文 / 2 翻译评阅）")
    s.set_defaults(func=cmd_oral_records)

    s = osub.add_parser("delete", help="删评阅记录（不带 --yes 只列不删）")
    s.add_argument("wm_ids", nargs="+", metavar="wmId")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_oral_delete)

    # ---- trans-review：翻译评阅（op36）----
    #
    # 名字**不是** "post-edit"：op36 只打分，不产出译文。早期文档的
    # "译后编辑" 是错的，见 lib/unipus_aigc/trans_review.py。
    tp = sub.add_parser("trans-review", aliases=["tr"],
                        help="翻译评阅（打分，op36）。**不产出译文**")
    tsub = tp.add_subparsers(dest="subcmd", required=True)

    def _add_tr_args(p, wait_default=None, with_wm_id=False):
        # choices 用 argparse 挡一道，让写错的语种码在这里就是退出码 2（用法错误），
        # 而不是发到平台上换回一个假分数。SDK 里 TransReviewAPI 还会再挡一道。
        p.add_argument("--src-lang", default="en", dest="src_lang",
                       choices=list(SUPPORTED_LANGS),
                       help="原文语种，**必须小写两字母** en/zh（默认 en）。"
                            "写错不报错，只给假分数")
        p.add_argument("--tgt-lang", default="zh", dest="tgt_lang",
                       choices=list(SUPPORTED_LANGS),
                       help="译文语种，**必须小写两字母** en/zh（默认 zh）")
        p.add_argument("--src-file", default=None, dest="src_file",
                       help="题目原文文件（与 --src-text 二选一）")
        p.add_argument("--src-text", default=None, dest="src_text",
                       help="直接给题目原文")
        p.add_argument("--tgt-file", default=None, dest="tgt_file",
                       help="待评译文文件（与 --tgt-text 二选一，二者必有一）")
        p.add_argument("--tgt-text", default=None, dest="tgt_text",
                       help="直接给待评译文")
        p.add_argument("--title", default=None,
                       help="记录标题；不给按时间戳生成")
        if with_wm_id:
            p.add_argument("--wm-id", default=None, dest="wm_id",
                           help="复用已有记录。**它的语言对必须跟 --src-lang/"
                                "--tgt-lang 一致**，否则直接报错（平台上只会静默给假分）")
        if wait_default is not None:
            _add_wait(p, wait_default)

    s = tsub.add_parser("review", help="一次性评阅：提交 + 出分（几秒）")
    _add_tr_args(s, 120, with_wm_id=True)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_trans_review_review)

    s = tsub.add_parser("submit", help="只提交，返回 wmId + taskId")
    _add_tr_args(s, with_wm_id=True)
    s.set_defaults(func=cmd_trans_review_submit)

    for name, help_text, func in (
            ("poll", "短轮询评阅结果", cmd_trans_review_poll),
            ("get", "只查一次，不轮询", cmd_trans_review_get)):
        s = tsub.add_parser(name, help=help_text)
        s.add_argument("task_id", metavar="taskId")
        if name == "poll":
            _add_wait(s, 60)
        s.add_argument("--json", action="store_true")
        s.set_defaults(func=func)

    s = tsub.add_parser("detail", help="记录详情（**这一档 evaluation 在顶层且填好了**）")
    s.add_argument("wm_id", metavar="wmId")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_trans_review_detail)

    s = tsub.add_parser("records", help="列出评阅记录（type=\"2\"）")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=10)
    s.add_argument("--type", default="2",
                   help="记录类型，默认 2 翻译评阅（1 作文 / 3 口语）")
    s.set_defaults(func=cmd_trans_review_records)

    s = tsub.add_parser("delete", help="删评阅记录（不带 --yes 只列不删）")
    s.add_argument("wm_ids", nargs="+", metavar="wmId")
    s.add_argument("--yes", action="store_true", help="确认删除")
    s.set_defaults(func=cmd_trans_review_delete)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MissingTokenError as e:
        print(str(e), file=sys.stderr)
        return EXIT_ERROR
    except StillRunning as e:
        # 不是错误：状态在平台侧，稍后用同一个 id 再来一次
        print(str(e), file=sys.stderr)
        return EXIT_STILL_RUNNING
    except TaskTimeout as e:
        print(f"仍在处理中（等待超时）: {e}", file=sys.stderr)
        return EXIT_STILL_RUNNING
    except TaskFailed as e:
        print(f"任务失败: {e}", file=sys.stderr)
        return EXIT_ERROR
    except AigcError as e:
        print(f"接口错误: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
