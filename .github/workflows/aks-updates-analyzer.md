---
on:
  schedule: weekly on monday around 9:00 utc+9
  workflow_dispatch:
description: "Weekly AKS updates analyzer that checks Azure Updates RSS and GitHub AKS changelog, then creates an issue with impact analysis for this repository."
labels: [aks, automation]
permissions:
  contents: read
engine:
  id: copilot
  model: claude-opus-4.6
network:
  allowed:
    - defaults
    - github
    - "www.microsoft.com"
tools:
  bash: ["python3"]
safe-outputs:
  create-issue:
    title-prefix: "[AKS Updates] "
    labels: [aks-updates, automation]
    close-older-issues: true
    max: 1
timeout-minutes: 15
---

# AKS Updates 週次分析

あなたは Azure Kubernetes Service (AKS) のアップデート情報を収集・分析するエキスパートです。
以下の手順に従い、このリポジトリに影響する AKS アップデートを分析し、日本語で GitHub Issue を作成してください。

## Step 1: Azure Updates RSS フィードから AKS 関連エントリを取得

以下の Python スクリプトで Azure Updates RSS フィードの取得と AKS 関連エントリの抽出を一括で実行してください。
**重要**: curl ではなく必ず以下の python3 スクリプトを使ってください。

```bash
python3 << 'PYEOF'
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import sys

url = "https://www.microsoft.com/releasecommunications/api/v2/azure/rss"
headers = {
    "Accept": "application/rss+xml",
    "User-Agent": "AKS-Updates-Analyzer/1.0"
}

try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    print(f"RSS feed downloaded: {len(data)} bytes", file=sys.stderr)
except Exception as e:
    print(f"RSS feed download failed: {e}", file=sys.stderr)
    print("[]")
    sys.exit(0)

root = ET.fromstring(data)
now = datetime.now(timezone.utc)
week_ago = now - timedelta(days=7)

items = root.findall(".//item")
keywords = ["kubernetes", "aks", "k8s", "container service"]
aks_items = []

for item in items:
    title = item.find("title").text or ""
    desc = item.find("description").text or ""
    link = item.find("link").text or ""
    pub_date_str = item.find("pubDate").text or ""
    text = (title + " " + desc).lower()
    if any(kw in text for kw in keywords):
        try:
            pub_date = parsedate_to_datetime(pub_date_str)
            if pub_date >= week_ago:
                aks_items.append({
                    "title": title.strip(),
                    "date": pub_date_str,
                    "link": link,
                    "desc": desc.strip()[:500]
                })
        except Exception:
            pass

print(json.dumps(aks_items, indent=2, ensure_ascii=False))
PYEOF
```

## Step 2: GitHub AKS リリースノートを取得

GitHub の Azure/AKS リポジトリから最新のリリースノートを取得してください。
GitHub MCP ツール（github-get_latest_release や github-list_releases）を使うか、以下の Python スクリプトで取得してください。

```bash
python3 << 'PYEOF'
import urllib.request
import sys

url = "https://github.com/Azure/AKS/releases"
req = urllib.request.Request(url, headers={"User-Agent": "AKS-Updates-Analyzer/1.0"})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(30000).decode("utf-8", errors="replace")
    print(data)
except Exception as e:
    print(f"Failed to fetch AKS releases: {e}", file=sys.stderr)
PYEOF
```

最新のリリース（直近1〜2週間以内に公開されたもの）の内容を分析してください。

## Step 3: リポジトリの現在の AKS 構成を確認

以下のファイルを読み取り、このリポジトリの AKS 構成を把握してください:

1. **`infra/modules/aks.bicep`** — Kubernetes バージョン、AKS API バージョン、使用中の機能（CNI、ネットワークポリシー、VPA、API Server VNet Integration 等）
2. **`infra/main.bicep`** — パラメータのデフォルト値
3. **`k8s/`** ディレクトリ配下 — 使用中の Kubernetes リソース（CiliumNetworkPolicy、HPA、PDB、Ingress 等）
4. **`.github/workflows/ci.yml`** — CI で使用している Kubernetes バージョン（kubeconform）

特に以下の情報を把握してください:
- 現在の Kubernetes バージョン
- 使用中の AKS 機能（Cilium CNI Overlay、Workload Identity、API Server VNet Integration、Web App Routing、VPA 等）
- AKS モード（Base / Automatic）
- Windows ノードの使用有無
- LocalDNS の使用有無
- Node Auto-Provisioning (NAP) の使用有無

## Step 4: 影響度分析

Step 1〜3 の情報を照合し、各アップデートを以下のカテゴリに分類してください:

- 🔴 **要対応**: リポジトリに直接影響し、セキュリティ修正やバージョン更新等のアクションが必要なもの
- 🟡 **認識しておくべき**: 間接的な影響があり、将来の計画に考慮すべきもの
- ⚪ **影響なし**: このリポジトリの構成では影響がないもの

各項目には具体的な推奨アクションと、元ソースへの Markdown リンク（`[タイトル](URL)`）を必ず含めてください。

## Step 5: Issue を作成

以下の形式で日本語の Issue を作成してください。

### Issue タイトル
`週次 AKS アップデート分析 (YYYY-MM-DD)`

### Issue 本文の構成

```markdown
## 📊 週次 AKS アップデート分析

**分析期間**: YYYY-MM-DD 〜 YYYY-MM-DD
**データソース**: Azure Updates RSS / GitHub AKS Changelog

### リポジトリの現在構成
| 項目 | 値 |
|------|-----|
| Kubernetes バージョン | x.xx |
| AKS API バージョン | xxxx-xx-xx-preview |
| CNI | Azure CNI Overlay + Cilium |
| ... | ... |

### 🔴 要対応

| # | アップデート | 影響 | 推奨アクション |
|---|------------|------|---------------|
| 1 | [タイトル](URL) | ... | ... |

### 🟡 認識しておくべき

| # | アップデート | 影響 | 推奨アクション |
|---|------------|------|---------------|
| 1 | [タイトル](URL) | ... | ... |

### ⚪ 影響なし

| # | アップデート | 理由 |
|---|------------|------|
| 1 | [タイトル](URL) | ... |

```

**重要**:
- 該当するアップデートがない場合でも、「今週は該当するアップデートはありませんでした」と Issue を作成してください
- テーブル内のリンクは Markdown リンク形式で記載してください
- 分析の根拠を明確に記載してください
