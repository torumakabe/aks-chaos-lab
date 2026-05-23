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
    - "azure.microsoft.com"
    - "learn.microsoft.com"
    - "releases.aks.azure.com"
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

以下の Python スクリプトで GitHub API から Azure/AKS リポジトリの最新リリースノートを取得してください。
**重要**: HTML スクレイピングではなく必ず以下の GitHub API スクリプトを使ってください。

```bash
python3 << 'PYEOF'
import urllib.request
import json
import sys
from datetime import datetime, timedelta, timezone

url = "https://api.github.com/repos/Azure/AKS/releases?per_page=5"
headers = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "AKS-Updates-Analyzer/1.0"
}

try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        releases = json.loads(resp.read())
    print(f"Fetched {len(releases)} releases from GitHub API", file=sys.stderr)
except Exception as e:
    print(f"GitHub API request failed: {e}", file=sys.stderr)
    print("[]")
    sys.exit(0)

now = datetime.now(timezone.utc)
two_weeks_ago = now - timedelta(days=14)
recent = []

for r in releases:
    pub = datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
    if pub >= two_weeks_ago:
        recent.append({
            "tag": r["tag_name"],
            "name": r["name"],
            "url": r["html_url"],
            "published_at": r["published_at"],
            "body": r.get("body", "")
        })

print(json.dumps(recent, indent=2, ensure_ascii=False))
PYEOF
```

出力にはリリースノートの全文（`body`）と URL が含まれます。以下の情報に注目して分析してください:

- **コンポーネントバージョン更新**（Cilium、ingress-nginx、Konnectivity、etcd 等）とそのセキュリティ修正（CVE）
- **Kubernetes パッチバージョン**の追加
- **Breaking Changes / 動作変更**
- **機能の非推奨化・廃止予告**
- **新リージョン対応**

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

Step 1〜3 の情報を照合し、**Step 1 と Step 2 で取得した全アップデートを漏れなく**以下のカテゴリに分類してください。

### 前提: 自動適用構成の解釈

「🔴 / 🟡」を判定する前に、`infra/modules/aks.bicep` から以下の自動適用構成を必ず確認してください。CVE 修正・コンポーネント更新であっても、対象が「マネージドコンポーネント」かつ以下の自動適用構成が有効な場合は、ユーザー操作なしにロールアウトされるため **🔴 ではなく 🟡 に分類**します。

- `autoUpgradeProfile.upgradeChannel` — Kubernetes コントロールプレーン / クラスタの自動更新
- `autoUpgradeProfile.nodeOSUpgradeChannel` — ノード OS イメージの自動更新（CVE-mitigated node image を含む）
- `Microsoft.ContainerService/managedClusters/maintenanceConfigurations` — `aksManagedAutoUpgradeSchedule` / `aksManagedNodeOSUpgradeSchedule` 等のスケジュール
- AKS マネージド add-on / アドオン相当のマネージドコンポーネント全般（Cilium、Azure Policy add-on、CSI drivers、cloud-provider-azure、Container Insights、Managed Prometheus collector、Application Routing、Cost-analysis agent 等）

なお「使用中」の判定は **bicep / k8s マニフェストでの明示的有効化が確認できるもの**のみ「使用中」とみなし、確認できない add-on / 機能は「未使用」として扱ってください（false-positive 防止）。

### カテゴリ定義

- 🔴 **要対応**: このリポジトリが**使用中**の機能・コンポーネントに影響し、かつ **ユーザー側のコード変更・運用操作が必要**なもの:
  - 非推奨化・廃止（Retirement / Deprecation）の影響を受けるもの
  - 破壊的変更（Breaking Changes）の影響を受けるもの
  - 自動適用構成の対象外であり、ユーザーが明示的にバージョン指定・パラメータ変更しない限り適用されない CVE 修正・更新
- 🟡 **認識しておくべき**: 使用中の機能に関連するが即座のユーザー操作は不要:
  - Kubernetes パッチバージョン更新
  - 新リージョン対応
  - 将来バージョンでの動作変更予告
  - マネージドコンポーネントの自動更新（CVE 修正を含むものも、上記「自動適用構成」が有効な範囲では本カテゴリに分類し、推奨アクションは「自動適用後の動作確認」とする）
- ⚪ **影響なし**: このリポジトリが**使用していない**機能に関するアップデート（明示的な有効化設定が無いものを含む）

各項目には具体的な推奨アクションと、元ソースへのリンクを必ず含めてください。CVE-related 項目を 🟡 に分類した場合は、推奨アクションに「(自動適用) `nodeOSUpgradeChannel: <設定値>` / メンテナンスウィンドウ <曜日・時刻> により適用済み想定。`az aks ...` または `kubectl ...` で対象バージョンの到達を確認」のように、自動適用の根拠と確認コマンドを併記してください。

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
