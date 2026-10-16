"""和 Lumina 裡的模型討論（開發者用）：方式二「Claude Code ↔ Lumina-Claude 討論設計，再交給 Gemini 實作」的討論那一步。

    python dev/discuss.py --model claude-sonnet-4-6 --file 訊息.md            開新討論
    python dev/discuss.py --cid <對話 id> --file 訊息.md                         接著同一段討論

用對話區（不是 Code 區）：對方只能討論、不能改檔案或執行指令。
送出前檢查 Claude 額度池，剩不到 --min-quota（預設 15%）就不送。最後印出 JSON：{"conversation", "reply", "tokens", "quota"}。
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8765"
HDR = {"Content-Type": "application/json", "Origin": API}


def get(path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(API + path, headers=HDR), timeout=30).read())


def quota(model):
    """這個模型所在額度池剩下的比例（取 5 小時與每週裡比較少的那個）。"""
    group = "Claude and GPT models" if any(k in model for k in ("claude", "gpt")) else "Gemini Models"
    for g in get("/api/usage").get("groups", []):
        if g["name"] == group:
            return min(b["remaining_fraction"] for b in g["buckets"])
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="要送出的訊息（Markdown 檔）")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--cid", help="接著哪一段討論")
    ap.add_argument("--min-quota", type=float, default=0.15)
    a = ap.parse_args()
    left = quota(a.model)
    if left is not None and left < a.min_quota:
        sys.exit(f"額度池只剩 {left:.0%}，低於 {a.min_quota:.0%}，不送出。")
    body = {"text": Path(a.file).read_text(encoding="utf-8"), "model": a.model, **({"conversation_id": a.cid} if a.cid else {})}
    reply, cid, tokens, err = "", a.cid, 0, None
    req = urllib.request.Request(API + "/api/chat", json.dumps(body).encode(), HDR)
    for line in urllib.request.urlopen(req, timeout=3600):
        e = json.loads(line)
        if e.get("event") == "result":
            r = e.get("result") or {}
            reply = r.get("response") or reply
            tokens += (r.get("usage") or {}).get("turn_tokens") or 0
        if e.get("event") == "done":
            cid, err = e.get("conversation_id") or cid, e.get("error")
    print(json.dumps({"conversation": cid, "reply": reply, "tokens": tokens, "error": err,
                      "quota_after": quota(a.model)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
