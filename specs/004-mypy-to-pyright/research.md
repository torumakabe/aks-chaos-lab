# Research: 型チェッカーをmypyからpyrightへ移行

**Feature**: 004-mypy-to-pyright  
**Date**: 2024-12-24

## 調査項目

### 1. pyright設定のベストプラクティス

**決定**: pyproject.tomlの`[tool.pyright]`セクションで設定を管理する

**根拠**: 
- pyrightは`pyrightconfig.json`と`pyproject.toml`の両方をサポート
- 既存のpyproject.tomlを使用することで設定ファイルを一元化できる
- Python 3.13対応が完了している

**検討した代替案**:
- `pyrightconfig.json`: 別ファイル管理が必要になるため却下

### 2. mypy設定からpyright設定への移行

**現在のmypy設定**:
```toml
[tool.mypy]
python_version = "3.13"
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true
strict_optional = true
```

**決定**: 以下のpyright設定に移行する

```toml
[tool.pyright]
pythonVersion = "3.13"
typeCheckingMode = "standard"
reportMissingImports = false
reportMissingTypeStubs = false
```

**根拠**:
- `typeCheckingMode = "standard"`: mypyのデフォルト設定に相当
- `reportMissingImports = false`: mypy の `ignore_missing_imports = true` に対応
- `reportMissingTypeStubs = false`: サードパーティライブラリのスタブ不足を許容

**検討した代替案**:
- `typeCheckingMode = "strict"`: より厳格だが、既存コードの修正が多くなる可能性があるため最初は避ける
- `typeCheckingMode = "basic"`: 緩すぎるため却下

### 3. uv での pyright インストール

**決定**: 開発依存関係として`pyright`パッケージを追加

**根拠**:
- uvは`pyright`をPyPIから直接インストール可能
- `uv run pyright`で実行可能
- types-requestsなどの型スタブは不要（pyrightは独自のバンドル型スタブを持つ）

### 4. Makefileの変更

**決定**: `mypy`コマンドを`pyright`に置き換える

**変更箇所**:
- `typecheck`ターゲット: `uv run mypy app/` → `uv run pyright app/`
- `qa`ターゲット: 同様に置き換え
- `clean`ターゲット: `.mypy_cache` → pyrightキャッシュディレクトリ（なし、pyrightはインメモリ）

### 5. 既存コードの互換性

**調査結果**: 現在のコードにはmypy固有のコメント `# type: ignore[call-arg]` が1箇所存在

**決定**: pyrightでも同じ構文が動作するため変更不要

**ファイル**: `src/app/main.py` 25行目
```python
return Settings()  # type: ignore[call-arg]
```

### 6. CI/CDへの影響

**調査結果**: GitHub Actionsワークフローでmypyを直接参照している箇所を確認

**決定**: 必要に応じてワークフローファイルも更新

## 結論

移行は直接的な設定変更で完了可能。主な変更点:

1. `pyproject.toml`: mypy設定削除、pyright設定追加、依存関係更新
2. `Makefile`: typecheck/qa/cleanターゲットの更新
3. `constitution.md`: 型チェッカーの記述をpyrightに更新
