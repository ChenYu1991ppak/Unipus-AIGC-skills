# -*- coding: utf-8 -*-
"""任务执行器：把一条**已经生成的**任务卡逐步跑一遍。

.. warning::
   这是本 plugin 里**唯一**会替用户发起平台调用的"自动"路径，所以它有三条
   刻意设计：

   1. **不给 ``--go`` 就只打印执行计划**，一个请求都不发（连 client 都不构造，
      所以没配凭证的机器上也能用来看计划）。
   2. **默认不代做 ``human`` 步骤**——学生的作文、译文、朗读都不该由程序生成。
      要演示整条链才加 ``--auto``，让 ``substitute`` 里的平台步骤顶上。
   3. **跑完必须打残留清单**：每个应用记录行的 id 原样列出来，
      并附上对应的清理命令。**执行器自己绝不删任何东西**，
      更不代按 ``--yes``。

长任务在这里是**逐步提交 + 逐步等待**：每一步本身就是一次秒级提交，
所以中断只丢"当前这一步的等待"，不丢已经拿到的产出。
进度落在任务卡旁边的 ``<NN>-<taskId>.run.json``，重跑时**跳过已完成的步骤**。
"""

import json
import os
import sys
import time

from .errors import AigcError, TaskTimeout
from .exercise import LocalCheckError

#: 每个应用的等待上限（秒）——**照各应用自己的默认值**，不统一成一个数。
WAIT_DEFAULTS = {
    "speech": 120,      # SpeechAPI.wait 默认 120
    "oral": 300,        # OralReviewAPI.review 默认 300
    "review": 300,      # ReviewAPI.essay 默认 300
    "tr": 120,          # TransReviewAPI.review 默认 120
    "questions": 180,   # QuestionGenAPI.generate 默认 180
    "translate": 120,   # TranslateAPI.text 默认 120
    "image": 180,       # ImageGenAPI.draw_and_wait 默认 180
    "article": 120,     # ArticleAPI 的 SSE 上限
    "local": 0,         # **不碰平台**：把上一步的产出原样搬过来，只用于 --auto
}

#: 每个调用在平台上会留下什么。``None`` = 零残留。
#: 值是 ``(字段名, 这是什么, 清理命令模板)``。
RESIDUE = {
    ("speech", "say"): ("id", "speech 记录", "speech delete <id>"),
    ("speech", "submit"): ("taskId", "speech 任务（记录稍后才有 id）",
                           "speech records 看 id"),
    ("oral", "review"): ("wmId", "wm 记录（口语评阅）", "oral delete <wmId>"),
    ("oral", "submit"): ("wmId", "wm 记录（口语评阅）", "oral delete <wmId>"),
    ("review", "essay"): ("wmId", "wm 记录（作文评阅）", "见 docs/call-chains.md §2"),
    ("review", "submit_essay"): ("wmId", "wm 记录（作文评阅）",
                                 "见 docs/call-chains.md §2"),
    ("tr", "review"): ("wmId", "wm 记录（翻译评阅）", "tr delete <wmId>"),
    ("tr", "submit"): ("wmId", "wm 记录（翻译评阅）", "tr delete <wmId>"),
    ("translate", "text"): ("id", "translate 记录（**type=1**）",
                            "`cleanup --ids <id> --yes`——**cleanup 现在会**"
                            "同时扫 includeText 的两种取值，所以看得见它"),
    ("questions", "create_material"): (None, "**阅读材料（永不可删）**",
                                       "平台没有 rm/delete"),
    ("questions", "generate"): (None, "出题记录（pid / generateCount 累加）",
                                "questions delete 只删题目，材料留着"),
    ("questions", "preview"): None,      # 不落库
    ("article", "common_continue"): None,   # SSE，不落库
    ("local", "take"): None,                # 不碰平台
    ("image", "draw_and_wait"): (None, "img 记录", "image delete <id>"),
}

#: 只读检查：这些调用**不需要** --go 就会执行（它们是"看现状"，不是"造数据"）。
READ_ONLY = {("questions", "materials")}


class StepFailed(AigcError):
    """某一步失败了。``run`` 会立刻停下来，不再跑后面的步骤。"""

    def __init__(self, message, *, step, ids=None, **kw):
        super().__init__(message, **kw)
        self.step = step
        self.ids = ids or {}


def _note(msg):
    print(msg, file=sys.stderr)


# ----------------------------------------------------------------------
# 计划（零平台调用）
# ----------------------------------------------------------------------
def plan_lines(task, *, auto=False, all_steps=False):
    """打印用的执行计划。**不构造 client、不发请求。**"""
    lines = [f"任务 {task['taskId']}　{task['title']}",
             f"链路 {task['chainName']}（`{task['chain']}`）",
             ""]
    if task.get("preconditions"):
        lines.append("⚠️ 跑之前要先满足：")
        lines += [f"  - {p}" for p in task["preconditions"]]
        lines.append("")

    todo = [s for s in task["steps"] if _will_run(s, auto=auto, all_steps=all_steps)]
    apps = sorted({(s.get("substitute") or s).get("app")
                   for s in todo if (s.get("substitute") or s).get("app")})
    lines.append(f"将向平台提交 "
                 f"{sum(1 for s in todo if (s.get('substitute') or s).get('app'))} 个任务，"
                 f"涉及应用：{'、'.join(apps) or '（无）'}")
    lines.append("")
    for step in task["steps"]:
        if not _will_run(step, auto=auto, all_steps=all_steps):
            why = ("人工步骤——**默认不代做**（要代做加 --auto）"
                   if step["kind"] == "human" else "可选步骤（要跑加 --all-steps）")
            lines.append(f"  第 {step['n']} 步 跳过　{step['label']}")
            lines.append(f"          {why}")
            continue
        call_step = step.get("substitute") or step
        call = (f"{call_step['app']}.{call_step['method']}"
                if call_step.get("app") else "（人工）")
        if step.get("substitute"):
            lines.append(f"  第 {step['n']} 步 代做　{step['label']}　[{call}]"
                         f"　← 学生那一步由程序顶上（只用于演示）")
        else:
            lines.append(f"  第 {step['n']} 步 执行　{step['label']}　[{call}]")
    lines.append("")
    lines.append(f"预计残留：{task['residue']}")
    lines.append("")
    lines.append("**这只是计划**——真要跑把 --go 加上。默认只出计划是为了不让"
                 "批量生成顺手变成批量提交。")
    return lines


def _will_run(step, *, auto, all_steps):
    if step["kind"] == "app":
        return True
    if step["kind"] == "app?":
        return bool(all_steps)
    if step["kind"] == "human":
        return bool(auto and step.get("substitute"))
    return False


# ----------------------------------------------------------------------
# 进度
# ----------------------------------------------------------------------
def progress_path(task_file):
    stem = task_file[:-len(".json")] if task_file.endswith(".json") else task_file
    return stem + ".run.json"


def load_progress(task_file):
    path = progress_path(task_file)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        _note(f"[警告] 进度文件读不出来（{e}），当作没跑过")
        return {}


def save_progress(task_file, data):
    path = progress_path(task_file)
    data["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# ----------------------------------------------------------------------
# 执行
# ----------------------------------------------------------------------
def run_task(task, task_file=None, *, go=False, auto=False, all_steps=False,
             wait=None, out=None):
    """跑一条任务。返回 ``(done, lines)``：``done`` 是逐步结果，``lines`` 是给用户看的。

    ``go=False`` 时只返回计划（``done`` 为空）。**零平台调用。**
    """
    if not go:
        return {}, plan_lines(task, auto=auto, all_steps=all_steps)

    problems = blocking_problems(task)
    if problems:
        raise LocalCheckError("这条任务还不能跑：\n  - " + "\n  - ".join(problems))

    progress = load_progress(task_file) if task_file else {}
    progress.setdefault("taskId", task["taskId"])
    steps_done = progress.setdefault("steps", {})

    lines = [f"任务 {task['taskId']}　{task['title']}", ""]
    ctx = {"steps": {}}
    cli = None

    for step in task["steps"]:
        n = step["n"]
        if not _will_run(step, auto=auto, all_steps=all_steps):
            why = ("人工步骤，默认不代做（要代做加 --auto）"
                   if step["kind"] == "human" else "可选步骤，默认不跑")
            if step["kind"] == "human" and step.get("brief"):
                lines.append(f"  第 {n} 步 **待人工**　{step['label']}")
                lines += [f"          {ln}"
                          for ln in _statement(step, ctx).splitlines()]
                lines.append(f"          → {why}")
            else:
                lines.append(f"  第 {n} 步 跳过　{step['label']}　（{why}）")
            continue

        done = steps_done.get(str(n))
        if done and done.get("status") == "ok":
            lines.append(f"  第 {n} 步 复用　{step['label']}　（上次已跑完，没重复提交）")
            ctx["steps"][n] = {"output": done.get("output")}
            continue

        if cli is None:
            from .client import UnipusAIGC
            cli = UnipusAIGC()

        t0 = time.time()
        try:
            out_val = _execute(cli, step, ctx, wait=wait or _wait_of(step))
        except TaskTimeout as e:
            # **不是失败**：任务在平台侧，稍后用同一个 id 再 poll
            lines.append(f"  第 {n} 步 仍在跑　{step['label']}")
            lines.append(f"          {e}")
            ids = (steps_done.get(str(n)) or {}).get("ids") or {}
            if ids:
                lines += [f"          {k}={v}" for k, v in ids.items()]
                lines.append("          → 用 `exercise run <taskId> --go` 再来一次，"
                             "已完成的步骤不会重复提交")
            print("\n".join(lines))
            raise
        except AigcError as e:
            steps_done[str(n)] = {"status": "failed", "error": str(e)}
            if task_file:
                save_progress(task_file, progress)
            lines.append(f"  第 {n} 步 失败　{step['label']}　（{time.time() - t0:.1f}s）")
            lines.append(f"          {e}")
            lines.append("")
            lines.append("**已停下，后面的步骤没有跑。**")
            lines += residue_lines(task, steps_done)
            print("\n".join(lines))
            raise StepFailed(f"第 {n} 步失败：{e}", step=n,
                             path=getattr(e, "path", None),
                             payload=getattr(e, "payload", None)) from e

        output, ids = out_val
        ctx["steps"][n] = {"output": output}
        steps_done[str(n)] = {"status": "ok", "output": output, "ids": ids}
        if task_file:
            save_progress(task_file, progress)
        lines.append(f"  第 {n} 步 完成　{step['label']}　（{time.time() - t0:.1f}s）")
        for k, v in ids.items():
            lines.append(f"          {k}={v}")
        for k, v in _brief_output(output).items():
            if k not in ids:                 # 别打两遍（audioUrl 两处都会出现）
                lines.append(f"          {k}={v}")

    lines.append("")
    lines += residue_lines(task, steps_done)
    if cli is not None:
        try:
            cli.close()
        except Exception:            # noqa: BLE001 —— 关不关得掉不该影响结果
            pass
    return steps_done, lines


def _statement(step, ctx):
    """人工步骤要发给学生的那段题干。

    任务卡里的 ``brief`` 在生成期只解析了 ``{{slot:…}}``——``{{stepN:…}}``
    （比如"题目：第 2 步出的那道题"）**留到执行期才解析**。这里就是解析点：
    解不出来就退回原文，别让这一步变成一片 ``{{…}}``。
    """
    from .exercise import resolve
    try:
        got = resolve(step["brief"], ctx, step["n"], phase="run")
    except AigcError:
        return step["brief"]
    return got if isinstance(got, str) else step["brief"]


def _wait_of(step):
    return WAIT_DEFAULTS.get(step.get("app"), 120)


def blocking_problems(task):
    """跑之前**本地**能判定的阻塞项。不发请求。"""
    from .exercise import check_task
    return list(check_task(task))


def _brief_output(output):
    """挑几个关键产出打出来（不打全文，几十词的东西刷屏）。"""
    if not isinstance(output, dict):
        return {}
    keep = ("score", "totalGrade", "audioUrl", "translation", "rmId")
    shown = {k: output[k] for k in keep if output.get(k) not in (None, "")}
    ev = output.get("evaluation")
    if isinstance(ev, dict) and ev.get("overall") is not None:
        shown["evaluation.overall"] = ev["overall"]
    items = output.get("items")
    if isinstance(items, list):
        shown["题数"] = len(items)
    if "translation" in shown and isinstance(shown["translation"], str):
        shown["translation"] = shown["translation"][:80]
    return shown


# ----------------------------------------------------------------------
# 派发：只调**已有的应用方法**
# ----------------------------------------------------------------------
def _execute(cli, step, ctx, *, wait):
    """跑一步，返回 ``(output, ids)``。

    ``human`` 步骤跑的是它的 ``substitute``（只有 ``--auto`` 才会走到这里），
    所以**实际调用取的是替身**，不是步骤本身。
    """
    from .exercise import resolve

    call = step.get("substitute") or step
    app, method = call.get("app"), call.get("method")
    if not app:
        raise LocalCheckError(f"第 {step['n']} 步是人工步骤，而且没有可用的替身")
    args = resolve(call.get("args") or {}, ctx, step["n"], phase="run")

    if app not in WAIT_DEFAULTS:
        raise LocalCheckError(f"第 {step['n']} 步的应用 {app!r} 不认识")

    try:
        return _dispatch(cli, app, method, args, wait)
    except ValueError as e:
        raise LocalCheckError(f"第 {step['n']} 步的参数没过本地校验"
                              f"（**没有发请求**）：{e}") from e


def _ploys_of(spec):
    code, _, count = str(spec).partition(":")
    return [{"code": int(code), "count": int(count or 1)}]


def _dispatch(cli, app, method, args, wait):
    a = dict(args)          # 局部副本——绝不动任务卡里那份

    if app == "local":
        # **不碰平台**：把指定的值原样交给下一步（`--auto` 代做人工步骤用）。
        # 它存在的唯一理由是：有些人工步骤的替身就是"把上一步的产出搬过来"，
        # 而那件事不该再绕一次平台。
        if method != "take":
            raise LocalCheckError(f"local.{method} 没有执行分支")
        # 按调用方给的 `key` 命名产出——下一步就是靠这个名字取值的
        key = a.get("key") or "content"
        return {key: a.get("value"), "content": a.get("value")}, {}

    if app == "speech":
        text, speaker = a.pop("text"), a.pop("speaker")
        sub = cli.speech.submit(text, speaker, **a)
        out = cli.speech.wait(sub["taskId"], timeout=wait)
        out.setdefault("taskId", sub["taskId"])
        return out, _pick_ids(out, ("id", "taskId"), ("audioUrl",))

    if app == "oral":
        audio = a.pop("audio")
        content = a.pop("content", None)
        ques_type = a.pop("ques_type", None)
        sub = cli.oral.submit(audio, content, content=content or "",
                              ques_type=ques_type)
        out = cli.oral.poll(sub["taskId"], timeout=wait)
        # ⚠️ `poll` 只回 queryTask 的内容（evaluation 那些），**没有 wmId**。
        # 残留清单要报的是 wm 记录，所以把 submit 那一步的 id 补回去。
        out.setdefault("wmId", sub["wmId"])
        return out, _pick_ids(out, ("wmId", "taskId"), ())

    if app == "review":
        content = a.pop("content")
        sub = cli.review.submit_essay(content, **a)
        out = cli.review.poll(sub["taskId"], timeout=wait)
        out.setdefault("wmId", sub["wmId"])      # poll 不回 wmId，见上
        return out, _pick_ids(out, ("wmId", "taskId"), ("score",))

    if app == "tr":
        sub = cli.trans_review.submit(a.pop("src_text"), a.pop("tgt_text"), **a)
        out = cli.trans_review.poll(sub["taskId"], timeout=wait)
        out.setdefault("wmId", sub["wmId"])      # poll 不回 wmId，见上
        return out, _pick_ids(out, ("wmId", "taskId"), ("score",))

    if app == "translate":
        detail = cli.translate.text(a.pop("text"), a.pop("from", "en"),
                                    a.pop("to", "zh"), timeout=wait)
        # ⚠️ `detail["translation"]` 是 `[{"src":…,"tgt":…}]` 的 **JSON 字符串**，
        # 直接往下喂会把那段 JSON 当译文。要用 `translated_text()` 取纯文本。
        text = cli.translate.translated_text(detail)
        out = {"id": detail.get("id"), "translation": text, "detail": detail}
        return out, _pick_ids(out, ("id",), ("translation",))

    if app == "questions":
        if method == "create_material":
            rm_id = cli.question_gen.create_material(a.pop("content"),
                                                     **a)
            return {"rmId": rm_id}, {"rmId": rm_id}
        if method == "preview":
            rm_id = a.pop("rm_id")
            ploys = _ploys_of(a.pop("ploy"))
            items = cli.question_gen.preview(rm_id, ploys, **a)
            return {"items": items}, {"题数": len(items)}
        if method == "generate":
            rm_id = a.pop("rm_id")
            ploys = _ploys_of(a.pop("ploy"))
            items, task_id = cli.question_gen.generate(rm_id, ploys, timeout=wait, **a)
            return {"items": items, "taskId": task_id}, \
                {"taskId": task_id, "题数": len(items)}
        # ⚠️ **故意没有 `answer` 分支。** 文档里的 `ques/ans` 实测 404
        # （两个主机都是，前端产物里也没有它），所以"答题"在任务卡里是
        # `human` 步骤，不走平台。留一个调不通的分支只会让人以为它能用。
        raise LocalCheckError(f"questions.{method} 没有执行分支")

    if app == "article":
        if method == "common_continue":
            text = cli.article.common_continue_text(**a)
            return {"content": text}, {"字数": len(text or "")}
        if method == "rewrite_text":
            text = cli.article.rewrite_text(**a)
            return {"content": text}, {"字数": len(text or "")}
        raise LocalCheckError(f"article.{method} 没有执行分支")

    if app == "image":
        prompt = a.pop("prompt")
        images = cli.image_gen.draw_and_wait(prompt, timeout=wait, **a)
        return {"imgList": images}, {"张数": len(images or [])}

    raise LocalCheckError(f"{app}.{method} 没有执行分支")


def _pick_ids(out, id_keys, extra):
    """从产出里挑出"平台上的句柄/关键结果"，用于报告与残留清单。

    **id 一律原样打全**（不截断）——残留清单里的 id 是要原样粘进
    ``* delete <id>`` 的，截断了就没法用。
    """
    ids = {}
    for k in list(id_keys) + list(extra):
        v = out.get(k) if isinstance(out, dict) else None
        if v:
            ids[k] = v
    return ids


# ----------------------------------------------------------------------
# 残留清单
# ----------------------------------------------------------------------
def residue_lines(task, steps_done):
    """**跑完必须打的东西**：平台上留下了什么、id 是什么、怎么清。"""
    rows, clean = [], []
    for step in task["steps"]:
        # 人工步骤跑的是替身，残留要按**替身**算
        call = step.get("substitute") or step
        if not call.get("app"):
            continue
        spec = RESIDUE.get((call["app"], call["method"]))
        if spec is None:
            continue
        field, what, how = spec
        done = steps_done.get(str(step["n"])) or {}
        if done.get("status") != "ok":
            continue
        ids = done.get("ids") or {}
        got = ids.get(field) if field else None
        if got:
            shown = f"{field}={got}"
        elif field is None:
            shown = "（本条不落库）"
        else:
            # 有记录、但我们没拿到它的 id —— 如实说，别假装没留下
            shown = f"（没拿到 {field}，用对应应用的 records 子命令去查）"
        rows.append(f"  {what}　{shown}")
        clean.append(f"  {how}")
        if field is None and what.startswith("**阅读材料"):
            rows[-1] += "　← **删不掉**"

    lines = ["平台残留："]
    lines += rows or ["  （无——这条链路的每一步都不落库）"]
    if clean:
        lines.append("清理（**都由你自己确认，执行器不代删、不代按 --yes**）：")
        for c in dict.fromkeys(clean):
            lines.append(f"  {c}")
    return lines
