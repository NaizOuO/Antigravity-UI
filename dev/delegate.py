"""派工入口（開發者用，不會打包）：Claude Code 把一件工作交給 Lumina Code，做完自己驗收。

    python dev/delegate.py <工作資料夾> --task "要做什麼" [--task-file 說明.md]
                           [--test "python acceptance_test.py ."] [--model ...] [--rounds 2] [--keep "main.py 的介面不能改"]

流程：
1. 把任務加上固定的派工說明（範圍、驗收方式、做完要交代什麼）送給 Lumina Code（介面伺服器要開著）
2. 權限詢問自動回答：能選「這段對話都允許」就選，不然「允許一次」
3. 做完後由這支程式自己重跑 --test（不採信模型說的），沒通過就把失敗輸出交回同一段對話再修，最多 --rounds 輪
4. 最後印出報告（JSON）：通過與否、改了哪些檔案、模型的回覆、時間、tokens、詢問次數
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8765"
HDR = {"Content-Type": "application/json", "Origin": API}
SKIP = {".venv", "__pycache__", ".git", ".agents", "node_modules", ".lumina"}

CONTRACT = ("\n\n---\n[派工說明] 這件工作是 Claude Code 交給你的，做完後它會自己重新驗收。\n"
            "- 先用 todo_write 把要做的事逐項列出（每個要求或症狀各一項），每項實際驗證過才勾掉。\n"
            "- 只改完成任務需要的檔案，不要重構或改動無關的部分。{keep}\n"
            "{test}"
            "- 做完用 design_review（phase=review）審查一次：範圍內的缺漏補上；範圍外的不要做，寫成建議。\n"
            "- 最後的回覆只要四段：①改了哪些檔案、各改了什麼（一行一個）②怎麼驗證的、結果 ③還沒解決或不確定的地方"
            "（沒有就寫「無」）④建議（尚未執行）：你認為還缺什麼、哪裡設計可以更好，給 Claude Code 參考（沒有就寫「無」）。")


def post(path, data, timeout=7200):
    return urllib.request.urlopen(urllib.request.Request(API + path, json.dumps(data).encode(), HDR), timeout=timeout)


def snapshot(root):
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP]
        for f in fns:
            p = Path(dp) / f
            try:
                out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
            except OSError:
                pass
    return out


def run_test(cmd, root):
    py = root / ".venv" / "Scripts" / "python.exe"
    if py.exists() and cmd.split()[0] in ("python", "py"):         # 用專案自己的虛擬環境跑
        cmd = f'"{py}" ' + cmd.split(None, 1)[1]
    r = subprocess.run(cmd, cwd=root, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=1800, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = (r.stdout + r.stderr).strip()
    ok = r.returncode == 0
    if "BENCH_RESULT" in out:                                       # 驗收程式用 BENCH_RESULT 回報時，以它為準
        res = json.loads(out.rsplit("BENCH_RESULT", 1)[1].strip().splitlines()[0])
        ok = res["passed"] == res["total"]
    return ok, out[-3000:]


def chat(text, model, ws, cid, stats):
    reply, err = "", None
    body = {"text": text, "model": model, "code": ws, **({"conversation_id": cid} if cid else {})}
    for line in post("/api/chat", body):
        e = json.loads(line)
        ev = e.get("event")
        su = e.get("step_update") or {}
        if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
            stats["tool_calls"] += 1
        if ev == "permission":
            stats["asks"] += 1
            post("/api/permission/answer", {"id": e["id"], "nonce": e["nonce"],
                                            "choice": "conv" if e.get("can_conv") else "once"}).read()
            print(f"  詢問（自動允許）：{e['command'][:120]}", file=sys.stderr, flush=True)
        if ev == "result":
            r = e.get("result") or {}
            reply = r.get("response") or reply
            stats["tokens"] += (r.get("usage") or {}).get("turn_tokens") or 0
        if ev == "done":
            cid, err = e.get("conversation_id") or cid, e.get("error")
    return reply, cid, err


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--task", default="")
    ap.add_argument("--task-file")
    ap.add_argument("--test", help="驗收指令（在工作資料夾執行，結束碼 0 或 BENCH_RESULT 全過算通過）")
    ap.add_argument("--keep", default="", help="不能改的東西（介面、檔案）")
    ap.add_argument("--model", default="gemini-3.8-flash-high")
    ap.add_argument("--rounds", type=int, default=2, help="驗收沒過時，交回去再修的最多輪數")
    a = ap.parse_args()
    root = Path(a.dir).resolve()
    task = (Path(a.task_file).read_text(encoding="utf-8") + "\n" if a.task_file else "") + a.task
    test = (f"- 驗收指令：在工作資料夾執行 {a.test}，全部通過才算完成；不要修改驗收用的測試檔。\n" if a.test else
            "- 自己寫測試或實際執行確認，正常情況和「不應該發生」的情況都要測。\n")
    prompt = task.strip() + CONTRACT.format(keep=f"不能改：{a.keep}。" if a.keep else "", test=test)

    ws = json.loads(post("/api/code/create", {"work_dir": str(root)}).read())
    if not ws.get("ok"):
        sys.exit(ws.get("message"))
    before, t0 = snapshot(root), time.time()
    stats = {"asks": 0, "tool_calls": 0, "tokens": 0}
    rounds, cid, passed, test_out = [], None, None, ""
    text = prompt
    for i in range(a.rounds + 1):
        print(f"第 {i + 1} 輪…", file=sys.stderr, flush=True)
        reply, cid, err = chat(text, a.model, ws["id"], cid, stats)
        if a.test:
            passed, test_out = run_test(a.test, root)
        rounds.append({"reply": reply, "error": err, "test_passed": passed})
        if err or not a.test or passed:
            break
        text = ("Claude Code 重新驗收沒有通過，以下是驗收指令的輸出。請找出原因修好，再跑一次驗收確認，"
                "回覆一樣用四段格式。\n```\n" + test_out[-2500:] + "\n```")
    after = snapshot(root)
    report = {
        "passed": passed, "rounds": len(rounds), "seconds": round(time.time() - t0), **stats,
        "conversation": cid, "workspace": ws["id"],
        "changed": sorted(k for k in after if k in before and after[k] != before[k]),
        "added": sorted(k for k in after if k not in before),
        "deleted": sorted(k for k in before if k not in after),
        "final_reply": rounds[-1]["reply"] if rounds else "", "error": rounds[-1]["error"] if rounds else None,
        "test_output_tail": test_out[-1500:],
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
