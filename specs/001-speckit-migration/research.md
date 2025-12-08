# Research: Spec Kit移行

**Date**: 2025-12-08  
**Feature**: 001-speckit-migration

## 調査項目

### 1. Spec Kit ディレクトリ構造の確認

**調査**: `.specify/` ディレクトリの現在の構造を確認

**結果**: 
```
.specify/
├── memory/
│   └── constitution.md      # テンプレート状態
├── scripts/
│   └── bash/
│       ├── check-prerequisites.sh
│       ├── create-new-feature.sh
│       ├── setup-plan.sh
│       └── update-agent-context.sh
├── templates/
│   ├── commands/
│   ├── plan-template.md
│   └── spec-template.md
└── .copilot-instructions.md  # Spec Kit固有の指示
```

**決定**: Spec Kitは正常にインストールされている。追加のセットアップは不要。

---

### 2. 旧ワークフローファイルの確認

**調査**: `.github/prompts/spec-driven-workflow-v1.md` の内容と参照箇所

**結果**:
- ファイルサイズ: 約300行
- 参照箇所: `.github/copilot-instructions.md` の「仕様駆動ワークフローの遵守」セクション

**決定**: 移行完了後に削除する。アーカイブは不要（Gitの履歴で参照可能）。

---

### 3. copilot-instructions.md と constitution.md の役割分離

**調査**: 両ファイルの現在の内容と重複

**結果**:
- `copilot-instructions.md`: AIへの指示 + 開発ルール（重複あり）
- `constitution.md`: テンプレート状態（未設定）

**決定**:
| 項目 | copilot-instructions.md | constitution.md |
|------|------------------------|-----------------|
| 応答スタイル（日本語） | ✅ | - |
| ファイル管理ルール | ✅ | - |
| ワークフロー参照 | ✅（Spec Kitへのポインタ） | - |
| 品質基準詳細 | - | ✅ |
| ドキュメント管理方針 | - | ✅ |
| ガバナンスルール | - | ✅ |

---

### 4. chatmodes ディレクトリの確認

**調査**: `.github/chatmodes/` の内容と参照箇所

**結果**:
- ファイル: `4.1-Beast.chatmode.md` のみ
- 参照箇所: なし（独立したファイル）
- 理由: モデル進化により不要、agentsへの移行が進行中

**決定**: ディレクトリごと削除する。

---

## 調査サマリー

| 項目 | 状態 | アクション |
|------|------|-----------|
| Spec Kit構造 | ✅ インストール済み | なし |
| 旧ワークフロー | 参照1箇所 | 削除 |
| 設定ファイル分離 | 重複あり | 役割分離 |
| chatmodes | 不要 | 削除 |

## 未解決事項

なし - すべての調査項目が解決済み
