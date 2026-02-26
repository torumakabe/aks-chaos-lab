#!/usr/bin/env python3
"""
Bicep What-If 変更抽出スクリプト

az deployment sub what-if を実行し、変更点を構造化JSONで出力する。
azd プロジェクトと単体 Bicep デプロイの両方に対応。

各変更には bicepDefinition フィールドが付与され、
Bicep ファイルでの定義状況を確認できる。

パターン定義は外部 JSON ファイル（patterns/noise_patterns.json）で管理。
AI エージェントがパターンを更新する際は、JSON ファイルを編集する。
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ロガー設定
logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """
    ロギングを設定する。

    Parameters:
        verbose: True の場合は DEBUG レベル、False の場合は WARNING レベル
    """
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


class NoisePatternLoader:
    """
    ノイズパターンを JSON ファイルから読み込むクラス。

    パターンは patterns/noise_patterns.json で管理される。
    共通パターンとリソースタイプ別パターンの2層構造。
    AI エージェントがパターンを更新する際は、JSON ファイルを編集する。

    パターンの使用状況は patterns/pattern_stats.json に記録される。
    """

    def __init__(self, patterns_file: str | None = None) -> None:
        """
        パターンローダーを初期化する。

        Parameters:
            patterns_file: パターン JSON ファイルのパス。
                           None の場合はスクリプトと同階層の patterns/noise_patterns.json を使用。
        """
        if patterns_file is None:
            script_dir = Path(__file__).parent
            patterns_file = str(script_dir / "patterns" / "noise_patterns.json")

        self._patterns_file = patterns_file
        self._data: dict[str, Any] | None = None

        # パターン統計ファイル
        patterns_dir = Path(patterns_file).parent
        self._stats_file = str(patterns_dir / "pattern_stats.json")
        self._stats: dict[str, Any] | None = None
        self._matched_patterns: set[str] = set()  # 今回マッチしたパターン

    def _load(self) -> dict[str, Any]:
        """パターンファイルを読み込む。"""
        if self._data is not None:
            return self._data

        try:
            with open(self._patterns_file, encoding="utf-8") as f:
                self._data = json.load(f)
                self._validate_patterns()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Failed to load patterns file: %s", e)
            self._data = {"common": {}, "resource_types": {}}

        return self._data

    def _validate_patterns(self) -> None:
        """パターンファイルの内容を検証し、警告を出す。"""
        if self._data is None:
            return

        warnings = []

        # 共通パターンの検証
        for pattern_type in [
            "readonly_patterns",
            "auto_managed_patterns",
            "custom_patterns",
        ]:
            patterns = self._data.get("common", {}).get(pattern_type, [])
            if isinstance(patterns, list):
                for idx, item in enumerate(patterns):
                    if isinstance(item, dict) and "pattern" in item:
                        pattern = item["pattern"]
                        if pattern.startswith("^properties\\."):
                            warnings.append(
                                f"⚠️  common.{pattern_type}[{idx}]: パターン '{pattern}' は "
                                f"'properties.' プレフィックスを含んでいます。スクリプトは自動的に除去するため、"
                                f"パターンからは除いてください。"
                            )

        # リソースタイプ別パターンの検証
        for resource_type, resource_patterns in self._data.get(
            "resource_types", {}
        ).items():
            for pattern_type in ["readonly_patterns"]:
                patterns = resource_patterns.get(pattern_type, [])
                if isinstance(patterns, list):
                    for idx, pattern in enumerate(patterns):
                        if isinstance(pattern, str) and pattern.startswith(
                            "^properties\\."
                        ):
                            warnings.append(
                                f"⚠️  resource_types.{resource_type}.{pattern_type}[{idx}]: "
                                f"パターン '{pattern}' は 'properties.' プレフィックスを含んでいます。"
                            )

            for pattern_type in ["auto_managed_patterns", "custom_patterns"]:
                patterns = resource_patterns.get(pattern_type, [])
                if isinstance(patterns, list):
                    for idx, item in enumerate(patterns):
                        if isinstance(item, dict) and "pattern" in item:
                            pattern = item["pattern"]
                            if pattern.startswith("^properties\\."):
                                warnings.append(
                                    f"⚠️  resource_types.{resource_type}.{pattern_type}[{idx}]: "
                                    f"パターン '{pattern}' は 'properties.' プレフィックスを含んでいます。"
                                )

            # known_defaults の path 検証
            known_defaults = resource_patterns.get("known_defaults", [])
            if isinstance(known_defaults, list):
                for idx, item in enumerate(known_defaults):
                    if isinstance(item, dict) and "path" in item:
                        path = item["path"]
                        if path.startswith("properties."):
                            warnings.append(
                                f"⚠️  resource_types.{resource_type}.known_defaults[{idx}]: "
                                f"path '{path}' は 'properties.' プレフィックスを含んでいます。"
                            )

        if warnings:
            logger.warning("=== パターンファイル検証警告 ===")
            for warning in warnings:
                logger.warning(warning)
            logger.warning("=" * 40)

    def _get_common(self) -> dict[str, Any]:
        """共通パターンを取得する。"""
        return self._load().get("common", {})

    def _get_resource_type(self, resource_type: str) -> dict[str, Any]:
        """リソースタイプ別パターンを取得する。"""
        return self._load().get("resource_types", {}).get(resource_type, {})

    def get_readonly_patterns(self, resource_type: str = "") -> list[str]:
        """readOnly プロパティパターンを返す（共通 + リソースタイプ別）。"""
        patterns = list(self._get_common().get("readonly_patterns", []))
        if resource_type:
            patterns.extend(
                self._get_resource_type(resource_type).get("readonly_patterns", [])
            )
        return patterns

    def get_arm_reference_patterns(self) -> list[str]:
        """ARM 参照式パターンを返す（共通のみ）。"""
        return self._get_common().get("arm_reference_patterns", [])

    def get_known_defaults(self, resource_type: str = "") -> list[tuple[str, Any, str]]:
        """既知のデフォルト値パターンを返す（共通 + リソースタイプ別）。"""
        results = []
        for item in self._get_common().get("known_defaults", []):
            results.append((item["path"], item["value"], item["description"]))
        if resource_type:
            for item in self._get_resource_type(resource_type).get(
                "known_defaults", []
            ):
                results.append((item["path"], item["value"], item["description"]))
        return results

    def get_custom_patterns(self, resource_type: str = "") -> list[tuple[str, str]]:
        """カスタム値パターンを返す（共通 + リソースタイプ別）。"""
        results = []
        for item in self._get_common().get("custom_patterns", []):
            results.append((item["pattern"], item["description"]))
        if resource_type:
            for item in self._get_resource_type(resource_type).get(
                "custom_patterns", []
            ):
                results.append((item["pattern"], item["description"]))
        return results

    def get_auto_managed_patterns(
        self, resource_type: str = ""
    ) -> list[tuple[str, str]]:
        """自動管理パターンを返す（共通 + リソースタイプ別）。"""
        results = []
        for item in self._get_common().get("auto_managed_patterns", []):
            results.append((item["pattern"], item["description"]))
        if resource_type:
            for item in self._get_resource_type(resource_type).get(
                "auto_managed_patterns", []
            ):
                results.append((item["pattern"], item["description"]))
        return results

    def record_pattern_match(
        self, pattern: str, category: str, resource_type: str = ""
    ) -> None:
        """パターンがマッチしたことを記録する。"""
        if resource_type:
            key = f"{resource_type}:{category}:{pattern}"
        else:
            key = f"{category}:{pattern}"
        self._matched_patterns.add(key)

    def _load_stats(self) -> dict[str, Any]:
        """統計ファイルを読み込む。"""
        if self._stats is not None:
            return self._stats

        try:
            with open(self._stats_file, encoding="utf-8") as f:
                self._stats = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._stats = {"patterns": {}, "lastRun": None}

        return self._stats

    def save_stats(self) -> None:
        """統計ファイルを保存する。"""
        stats = self._load_stats()
        now = datetime.now(timezone.utc).isoformat()

        stats["lastRun"] = now

        # マッチしたパターンの lastMatched を更新
        for key in self._matched_patterns:
            if key not in stats["patterns"]:
                stats["patterns"][key] = {"matchCount": 0, "firstMatched": now}
            stats["patterns"][key]["lastMatched"] = now
            stats["patterns"][key]["matchCount"] = (
                stats["patterns"][key].get("matchCount", 0) + 1
            )

        try:
            with open(self._stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
            logger.debug("Pattern stats saved to %s", self._stats_file)
        except OSError as e:
            logger.warning("Failed to save pattern stats: %s", e)

    def get_unused_patterns(self, days: int = 30) -> list[dict[str, Any]]:
        """
        指定日数以上使用されていないパターンを返す。

        Parameters:
            days: 未使用とみなす日数

        Returns:
            未使用パターンのリスト
        """
        stats = self._load_stats()
        now = datetime.now(timezone.utc)
        threshold = now - __import__("datetime").timedelta(days=days)
        unused = []

        for key, info in stats.get("patterns", {}).items():
            last_matched_str = info.get("lastMatched")
            if last_matched_str:
                last_matched = datetime.fromisoformat(last_matched_str.replace("Z", "+00:00"))
                if last_matched < threshold:
                    # キー形式: "resource_type:category:pattern" または "category:pattern"
                    parts = key.split(":")
                    if len(parts) >= 3:
                        resource_type = parts[0]
                        category = parts[1]
                        pattern = ":".join(parts[2:])
                    elif len(parts) == 2:
                        resource_type = None
                        category, pattern = parts
                    else:
                        continue  # 不正なキーはスキップ
                    entry = {
                        "category": category,
                        "pattern": pattern,
                        "lastMatched": last_matched_str,
                        "daysSinceLastMatch": (now - last_matched).days,
                    }
                    if resource_type:
                        entry["resourceType"] = resource_type
                    unused.append(entry)

        return sorted(unused, key=lambda x: x["daysSinceLastMatch"], reverse=True)


# グローバルパターンローダーインスタンス
_pattern_loader: NoisePatternLoader | None = None


def get_pattern_loader(patterns_file: str | None = None) -> NoisePatternLoader:
    """パターンローダーのインスタンスを取得する。"""
    global _pattern_loader
    if _pattern_loader is None:
        _pattern_loader = NoisePatternLoader(patterns_file)
    return _pattern_loader


def match_known_default(
    check_path: str,
    value: Any,
    known_defaults: list[tuple[str, Any, str]],
) -> str | None:
    """
    既知のデフォルト値に一致するかを判定する。

    Parameters:
        check_path: properties. を除去したパス
        value: 比較対象の値
        known_defaults: (path, value, description) のリスト

    Returns:
        一致した場合は description、未一致なら None
    """
    if value is None:
        return None

    path_end = check_path.split(".")[-1]
    for default_path, default_value, description in known_defaults:
        if path_end == default_path or check_path.endswith(default_path):
            if value == default_value:
                return description
    return None


def get_reference_info(
    path: str,
    before: Any,
    after: Any,
    bicep_definition: dict[str, Any],
    resource_type: str = "",
) -> str:
    """
    プロパティの参考情報を生成する。

    Bicep 照合の成否に関わらず、プロパティの性質に基づいた参考情報を優先する。
    外部 YAML パターンファイルが利用可能な場合はそれを使用する。

    Parameters:
        path: プロパティパス
        before: 変更前の値
        bicep_definition: Bicep 定義情報
        resource_type: リソースタイプ（オプション、より精密なマッチングに使用）

    Returns:
        参考情報の文字列（例: "⚠️ カスタムタグ"）
    """
    loader = get_pattern_loader()

    # properties. プレフィックスを除去
    check_path = path
    if check_path.startswith("properties."):
        check_path = check_path[len("properties.") :]

    # 1. カスタムパターンチェック（最優先）
    for pattern, description in loader.get_custom_patterns(resource_type):
        if re.search(pattern, check_path):
            loader.record_pattern_match(pattern, "custom_patterns", resource_type)
            return f"⚠️ {description}"

    # 2. readOnly チェック（リソースタイプ別パターンを使用）
    if is_readonly_property(path, resource_type):
        return "🔒 readOnly（Azure 自動設定）"

    # 3. Azure 自動設定の可能性が高いプロパティ
    for pattern, description in loader.get_auto_managed_patterns(resource_type):
        if re.search(pattern, check_path):
            loader.record_pattern_match(pattern, "auto_managed_patterns", resource_type)
            return f"📘 {description}"

    # 4. 既知のデフォルト値チェック
    known_defaults = loader.get_known_defaults(resource_type)
    default_description = match_known_default(check_path, before, known_defaults)
    if default_description is None:
        default_description = match_known_default(check_path, after, known_defaults)
    if default_description is not None:
        return f"📘 {default_description}"

    # 5. Bicep 定義情報（defined の場合のみ表示）
    bicep_status = bicep_definition.get("status", "unknown")
    if bicep_status == "defined":
        file_info = bicep_definition.get("file", "")
        line_info = bicep_definition.get("line", "")
        if file_info and line_info:
            return f"📍 Bicep 定義あり ({file_info}:{line_info})"
        return "📍 Bicep 定義あり"

    # 未分類の変更（パターンにマッチしない）
    return "❓ 未分類。確認推奨"


class BicepFileCache:
    """Bicep ファイルのキャッシュを管理するクラス。"""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._loaded_dir: str | None = None

    def load(self, bicep_dir: str) -> dict[str, str]:
        """
        Bicep ファイルを読み込んでキャッシュする。

        Returns:
            {ファイルパス: ファイル内容} の辞書
        """
        if self._cache and self._loaded_dir == bicep_dir:
            return self._cache

        self._cache.clear()
        self._loaded_dir = bicep_dir

        bicep_path = Path(bicep_dir)
        if not bicep_path.exists():
            return {}

        for bicep_file in bicep_path.rglob("*.bicep"):
            try:
                self._cache[str(bicep_file)] = bicep_file.read_text(encoding="utf-8")
            except (IOError, UnicodeDecodeError):
                continue

        return self._cache


# シングルトンインスタンス
_bicep_cache = BicepFileCache()


def load_bicep_files(bicep_dir: str) -> dict[str, str]:
    """
    Bicep ファイルを読み込んでキャッシュする。

    Returns:
        {ファイルパス: ファイル内容} の辞書
    """
    return _bicep_cache.load(bicep_dir)


def extract_search_terms(
    property_path: str,
) -> tuple[str, list[str], list[tuple[str, int]]]:
    """
    プロパティパスから検索キーワード、親コンテキスト、配列インデックス情報を抽出する。

    Returns:
        tuple[str, list[str], list[tuple[str, int]]]:
            - 検索キーワード
            - 親コンテキストのリスト（配列名のみ）
            - 配列インデックス情報のリスト（配列名, インデックス）

    Examples:
        "tags.CostControl" -> ("CostControl", ["tags"], [])
        "properties.networkSecurityGroup" -> ("networkSecurityGroup", [], [])
        "properties.subnets.1.properties.networkSecurityGroup"
            -> ("networkSecurityGroup", ["subnets"], [("subnets", 1)])
        "properties.agentPoolProfiles.0.securityProfile"
            -> ("securityProfile", ["agentPoolProfiles"], [("agentPoolProfiles", 0)])
        "properties.agentPoolProfiles.0.count"
            -> ("count", ["agentPoolProfiles"], [("agentPoolProfiles", 0)])
    """
    parts = property_path.split(".")

    # 配列インデックス情報を抽出
    array_indices: list[tuple[str, int]] = []
    for i, part in enumerate(parts):
        if part.isdigit() and i > 0:
            # 直前の部分が配列名
            array_name = parts[i - 1]
            if array_name not in ("properties",):
                array_indices.append((array_name, int(part)))

    # 意味のある部分（数字と properties を除外）を抽出
    meaningful_parts = [
        p for p in parts if not p.isdigit() and p not in ("properties",)
    ]

    if not meaningful_parts:
        return parts[-1], [], array_indices

    # 最後の部分が検索キーワード、それ以外が親コンテキスト
    search_term = meaningful_parts[-1]
    parent_context = meaningful_parts[:-1]

    return search_term, parent_context, array_indices


def extract_search_term(property_path: str) -> str:
    """
    プロパティパスから検索キーワードを抽出する（後方互換性のため維持）。

    Examples:
        "tags.CostControl" -> "CostControl"
        "properties.networkSecurityGroup" -> "networkSecurityGroup"
        "properties.subnets.1.properties.networkSecurityGroup" -> "networkSecurityGroup"
        "properties.agentPoolProfiles.0.count" -> "count"
    """
    term, _, _ = extract_search_terms(property_path)
    return term


def get_bicep_resource_pattern(resource_type: str) -> str | None:
    """
    リソースタイプから Bicep リソース宣言パターンを生成する。

    Parameters:
        resource_type: Azure リソースタイプ（例: Microsoft.Network/publicIPAddresses）

    Returns:
        Bicep リソース宣言の正規表現パターン、または None
    """
    if not resource_type:
        return None

    # Microsoft.Network/publicIPAddresses -> 'Microsoft.Network/publicIPAddresses@
    # リソース宣言は resource xxx 'Microsoft.xxx/yyy@api-version' の形式
    return f"'{resource_type}@"


def is_inside_parent_block(
    lines: list[str], target_line: int, parent_context: list[str]
) -> bool:
    """
    指定行が親コンテキストのブロック内にあるかを検証する。

    波括弧と角括弧のネストを追跡し、target_line が parent_context で始まる
    ブロック内にあるかどうかを判定する。

    Parameters:
        lines: ファイルの行リスト
        target_line: 検証対象の行番号（1-indexed）
        parent_context: 親コンテキストのリスト（例: ['agentPoolProfiles']）

    Returns:
        True: 親コンテキストのブロック内にある
        False: 親コンテキストのブロック内にない
    """
    if not parent_context:
        return True

    # 最も近い親コンテキストを使用
    parent = parent_context[-1]

    # target_line より前の行を逆順に探索し、親コンテキストのブロック開始を探す
    target_idx = target_line - 1  # 0-indexed

    # 親コンテキストの定義行を探す
    parent_line_idx = -1
    for i in range(target_idx - 1, -1, -1):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith(f"{parent}:") or stripped.startswith(f"{parent} :"):
            parent_line_idx = i
            break

    if parent_line_idx == -1:
        # 親コンテキストが見つからない
        return False

    # 親行から target_line までの括弧を追跡
    parent_line = lines[parent_line_idx]

    # 親行で開始された括弧の数
    open_brace = parent_line.count("{") - parent_line.count("}")
    open_bracket = parent_line.count("[") - parent_line.count("]")

    # 親行から target_line までの間で括弧を追跡
    for j in range(parent_line_idx + 1, target_idx):
        check_line = lines[j]
        open_brace += check_line.count("{") - check_line.count("}")
        open_bracket += check_line.count("[") - check_line.count("]")

        # 親ブロックが閉じた場合（両方のカウントが0以下になった場合）
        # target_line に到達する前に閉じていれば、target_line は親ブロック外
        if open_brace <= 0 and open_bracket <= 0:
            return False

    # target_line に到達しても親ブロックが開いていれば、ブロック内
    return open_brace > 0 or open_bracket > 0


def find_array_element_range(
    lines: list[str], array_name: str, element_index: int, start_search: int = 0
) -> tuple[int, int] | None:
    """
    Bicep ファイル内で配列の特定インデックスの要素範囲を見つける。

    Parameters:
        lines: ファイルの行リスト
        array_name: 配列名（例: 'subnets'）
        element_index: 要素のインデックス（0-based）
        start_search: 検索開始行（0-indexed）

    Returns:
        (開始行, 終了行) のタプル（1-indexed）、見つからない場合は None
    """
    # 配列定義を探す
    array_start_idx = -1
    for i in range(start_search, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith(f"{array_name}:") or stripped.startswith(f"{array_name} :"):
            array_start_idx = i
            break

    if array_start_idx == -1:
        return None

    # 配列の開始（[）を探す
    bracket_start_idx = -1
    for i in range(array_start_idx, min(array_start_idx + 3, len(lines))):
        if "[" in lines[i]:
            bracket_start_idx = i
            break

    if bracket_start_idx == -1:
        return None

    # 配列要素を追跡（波括弧で区切られた要素を数える）
    current_element = -1
    element_start = -1
    brace_depth = 0
    in_element = False

    for i in range(bracket_start_idx, len(lines)):
        line = lines[i]

        for char_idx, char in enumerate(line):
            if char == "[" and i == bracket_start_idx:
                # 配列の開始
                continue
            elif char == "]" and brace_depth == 0:
                # 配列の終了
                return None
            elif char == "{":
                if brace_depth == 0:
                    # 新しい要素の開始
                    current_element += 1
                    if current_element == element_index:
                        element_start = i + 1  # 1-indexed
                        in_element = True
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0 and in_element:
                    # 対象要素の終了
                    return (element_start, i + 1)  # 1-indexed

    return None


def is_inside_array_element(
    lines: list[str],
    target_line: int,
    array_indices: list[tuple[str, int]],
    resource_ranges: list[tuple[int, int]],
) -> bool:
    """
    指定行が配列の特定インデックスの要素内にあるかを検証する。

    Parameters:
        lines: ファイルの行リスト
        target_line: 検証対象の行番号（1-indexed）
        array_indices: 配列インデックス情報のリスト（配列名, インデックス）
        resource_ranges: リソースブロック範囲のリスト

    Returns:
        True: すべての配列インデックス条件を満たす
        False: 条件を満たさない
    """
    if not array_indices:
        return True

    # リソースブロック内の開始位置を取得
    start_search = 0
    for start, end in resource_ranges:
        if start <= target_line <= end:
            start_search = start - 1  # 0-indexed
            break

    # 各配列インデックスを検証
    for array_name, element_index in array_indices:
        element_range = find_array_element_range(
            lines, array_name, element_index, start_search
        )
        if element_range is None:
            # 配列要素が見つからない = 定義されていない
            return False

        elem_start, elem_end = element_range
        if not (elem_start <= target_line <= elem_end):
            # target_line がこの配列要素の範囲外
            return False

    return True


def find_resource_block_range(
    lines: list[str], resource_type: str
) -> list[tuple[int, int]]:
    """
    Bicep ファイル内で特定のリソースタイプの定義ブロック範囲を見つける。

    Parameters:
        lines: ファイルの行リスト
        resource_type: Azure リソースタイプ

    Returns:
        (開始行, 終了行) のリスト（1-indexed）
    """
    pattern = get_bicep_resource_pattern(resource_type)
    if not pattern:
        return []

    ranges = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # リソース宣言を検出
        if "resource " in line and pattern in line:
            start_line = i + 1  # 1-indexed
            # ブロックの終了を探す（波括弧のネストを追跡）
            brace_count = 0
            found_open = False
            end_line = start_line

            for j in range(i, len(lines)):
                for char in lines[j]:
                    if char == "{":
                        brace_count += 1
                        found_open = True
                    elif char == "}":
                        brace_count -= 1

                if found_open and brace_count == 0:
                    end_line = j + 1  # 1-indexed
                    break

            ranges.append((start_line, end_line))
            i = end_line
        else:
            i += 1

    return ranges


def find_bicep_definition(
    property_path: str, bicep_dir: str = "./infra", resource_type: str = ""
) -> dict[str, Any]:
    """
    プロパティが Bicep ファイルで定義されているか検索する。

    親コンテキスト（例: agentPoolProfiles）を考慮し、
    階層構造が一致するマッチを優先する。
    resource_type が指定された場合、該当リソースの定義ブロック内のみを検索する。

    Returns:
        {
            "status": "defined" | "notDefined" | "unknown",
            "file": "infra/modules/aks.bicep" | null,
            "line": 123 | null,
            "context": "tags: { ... }" | null
        }
    """
    bicep_files = load_bicep_files(bicep_dir)
    if not bicep_files:
        return {
            "status": "unknown",
            "file": None,
            "line": None,
            "context": None,
            "reason": "No Bicep files found",
        }

    search_term, parent_context, array_indices = extract_search_terms(property_path)
    if not search_term:
        return {
            "status": "unknown",
            "file": None,
            "line": None,
            "context": None,
            "reason": "Could not extract search term",
        }

    matches: list[dict[str, Any]] = []
    context_matches: list[dict[str, Any]] = []

    for file_path, content in bicep_files.items():
        lines = content.split("\n")

        # リソースタイプが指定されている場合、該当ブロック範囲を取得
        resource_ranges: list[tuple[int, int]] = []
        if resource_type:
            resource_ranges = find_resource_block_range(lines, resource_type)
            # このファイルに該当リソースがない場合はスキップ
            if not resource_ranges:
                continue

        for line_num, line in enumerate(lines, start=1):
            # リソースタイプが指定されている場合、ブロック内かチェック
            if resource_type and resource_ranges:
                in_resource_block = any(
                    start <= line_num <= end for start, end in resource_ranges
                )
                if not in_resource_block:
                    continue

            # プロパティ名が含まれているか確認
            # コメント行は除外
            stripped = line.strip()
            if stripped.startswith("//"):
                continue

            # 検索キーワードがある場合
            if search_term in line:
                # コンテキストを取得（前後の行を探索）
                # 親コンテキストの検証用に広めの範囲を取得
                context_start = max(0, line_num - 50)
                context_end = min(len(lines), line_num + 5)
                extended_context = "\n".join(lines[context_start:context_end])

                # 表示用コンテキスト（前後2行）
                display_start = max(0, line_num - 3)
                display_end = min(len(lines), line_num + 2)
                display_context = "\n".join(lines[display_start:display_end])

                match_info = {
                    "file": file_path,
                    "line": line_num,
                    "context": display_context,
                    "extended_context": extended_context,
                }

                # 親コンテキストがある場合、そのブロック内にあるか厳密に検証
                if parent_context:
                    # 波括弧のネストを追跡して、親コンテキストのブロック内にあるか確認
                    if not is_inside_parent_block(lines, line_num, parent_context):
                        matches.append(match_info)
                        continue

                    # 配列インデックスがある場合、特定の要素内にあるか検証
                    if array_indices:
                        if not is_inside_array_element(
                            lines, line_num, array_indices, resource_ranges
                        ):
                            matches.append(match_info)
                            continue

                    context_matches.append(match_info)
                else:
                    # 配列インデックスがある場合、特定の要素内にあるか検証
                    if array_indices:
                        if is_inside_array_element(
                            lines, line_num, array_indices, resource_ranges
                        ):
                            context_matches.append(match_info)
                        else:
                            matches.append(match_info)
                    else:
                        matches.append(match_info)

    # 親コンテキストにマッチしたものを優先
    if context_matches:
        if len(context_matches) == 1:
            return {
                "status": "defined",
                "file": context_matches[0]["file"],
                "line": context_matches[0]["line"],
                "context": context_matches[0]["context"],
                "searchTerm": search_term,
                "parentContext": parent_context,
            }
        # 複数の親コンテキストマッチがある場合
        return {
            "status": "unknown",
            "file": context_matches[0]["file"],
            "line": context_matches[0]["line"],
            "context": context_matches[0]["context"],
            "matchCount": len(context_matches),
            "searchTerm": search_term,
            "parentContext": parent_context,
            "reason": f"Multiple context matches found ({len(context_matches)})",
        }

    # 親コンテキストマッチがない場合
    if not matches:
        return {
            "status": "notDefined",
            "file": None,
            "line": None,
            "context": None,
            "searchTerm": search_term,
            "parentContext": parent_context,
        }

    # 親コンテキストがあるのに親コンテキストマッチがない場合は notDefined
    # （異なる階層で同名プロパティが定義されている場合）
    if parent_context:
        return {
            "status": "notDefined",
            "file": None,
            "line": None,
            "context": None,
            "searchTerm": search_term,
            "parentContext": parent_context,
            "reason": f"Found {len(matches)} match(es) but none in {parent_context} context",
        }

    if len(matches) == 1:
        return {
            "status": "defined",
            "file": matches[0]["file"],
            "line": matches[0]["line"],
            "context": matches[0]["context"],
            "searchTerm": search_term,
        }

    # 複数マッチの場合は最初のものを返し、unknown とする
    return {
        "status": "unknown",
        "file": matches[0]["file"],
        "line": matches[0]["line"],
        "context": matches[0]["context"],
        "matchCount": len(matches),
        "searchTerm": search_term,
        "reason": f"Multiple matches found ({len(matches)})",
    }


def get_azd_env_values() -> dict[str, str]:
    """azd env get-values から環境変数を取得する。"""
    try:
        result = subprocess.run(
            ["azd", "env", "get-values"],
            capture_output=True,
            text=True,
            check=True,
        )
        values = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, _, value = line.partition("=")
                # クォートを除去
                values[key] = value.strip("\"'")
        return values
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}


def detect_azd_project() -> tuple[bool, dict[str, str]]:
    """azd プロジェクトかどうかを検出し、環境変数を返す。"""
    if not os.path.exists("azure.yaml"):
        return False, {}

    env_values = get_azd_env_values()
    if not env_values:
        return False, {}

    return True, env_values


def run_what_if(
    template: str,
    location: str,
    subscription: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """az deployment sub what-if を実行し、結果を返す。"""
    cmd = [
        "az",
        "deployment",
        "sub",
        "what-if",
        "--location",
        location,
        "--template-file",
        template,
        "--output",
        "json",
        "--no-pretty-print",
    ]

    if subscription:
        cmd.extend(["--subscription", subscription])

    if parameters:
        # パラメータを key=value 形式で渡す
        for key, value in parameters.items():
            cmd.extend(["--parameters", f"{key}={value}"])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # エラーでも JSON が返る場合がある
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"what-if failed: {result.stderr}")

    return json.loads(result.stdout)


def is_readonly_property(path: str, resource_type: str = "") -> bool:
    """パスが ARM 共通 readOnly プロパティかどうかを判定する。"""
    loader = get_pattern_loader()

    # properties. プレフィックスを除去して判定
    check_path = path
    if check_path.startswith("properties."):
        check_path = check_path[len("properties.") :]

    for pattern in loader.get_readonly_patterns(resource_type):
        if re.search(pattern, check_path):
            loader.record_pattern_match(pattern, "readonly_patterns", resource_type)
            return True
    return False


def contains_arm_reference(value: Any) -> bool:
    """値に ARM 参照式が含まれるかどうかを判定する。"""
    loader = get_pattern_loader()

    if not isinstance(value, str):
        return False

    for pattern in loader.get_arm_reference_patterns():
        if re.search(pattern, value):
            loader.record_pattern_match(pattern, "arm_reference_patterns")
            return True
    return False


def evaluate_property_change(
    path: str, change_type: str, before: Any, after: Any, resource_type: str = ""
) -> dict[str, Any]:
    """
    プロパティ変更を評価し、evaluation フィールドを返す。

    Returns:
        {
            "status": "pending" | "noise_confirmed" | "drift_candidate",
            "reason": null | "readOnly" | "armReference" | "noEffect",
            "confidence": "high" | "low"
        }
    """
    # NoEffect は影響なし（ARM が無視する変更）
    if change_type == "NoEffect":
        return {
            "status": "noise_confirmed",
            "reason": "noEffect",
            "confidence": "high",
        }

    # ARM 共通 readOnly プロパティ（リソースタイプ別パターンを使用）
    if is_readonly_property(path, resource_type):
        return {
            "status": "noise_confirmed",
            "reason": "readOnly",
            "confidence": "high",
        }

    # ARM 参照式を含む場合
    if contains_arm_reference(before) or contains_arm_reference(after):
        return {
            "status": "noise_confirmed",
            "reason": "armReference",
            "confidence": "high",
        }

    # それ以外は pending（後続ステップで評価が必要）
    return {
        "status": "pending",
        "reason": None,
        "confidence": None,
    }


def flatten_property_changes(
    delta: list[dict[str, Any]] | None,
    prefix: str = "",
    bicep_dir: str = "./infra",
    resource_type: str = "",
) -> list[dict[str, Any]]:
    """ネストされたプロパティ変更をフラット化し、evaluation と bicepDefinition を付与する。"""
    if not delta:
        return []

    changes = []

    for item in delta:
        path = item.get("path", "")
        full_path = f"{prefix}.{path}" if prefix else path
        change_type = item.get("propertyChangeType", "Unknown")

        # 子要素がある場合は再帰
        children = item.get("children", [])
        if children:
            changes.extend(
                flatten_property_changes(children, full_path, bicep_dir, resource_type)
            )
        else:
            before = item.get("before")
            after = item.get("after")
            evaluation = evaluate_property_change(
                full_path, change_type, before, after, resource_type
            )

            # Bicep 照合（リソースタイプを渡して正確なマッチを行う）
            bicep_definition = find_bicep_definition(
                full_path, bicep_dir, resource_type
            )

            # 参考情報を生成
            reference_info = get_reference_info(
                full_path,
                before,
                after,
                bicep_definition,
                resource_type,
            )

            changes.append(
                {
                    "path": full_path,
                    "changeType": change_type,
                    "before": before,
                    "after": after,
                    "evaluation": evaluation,
                    "bicepDefinition": bicep_definition,
                    "referenceInfo": reference_info,
                }
            )

    return changes


def extract_resource_changes(
    what_if_result: dict[str, Any], bicep_dir: str = "./infra"
) -> list[dict[str, Any]]:
    """what-if 結果からリソース変更を抽出する。"""
    changes = []

    for change in what_if_result.get("changes", []):
        resource_id = change.get("resourceId", "")
        change_type = change.get("changeType", "Unknown")

        # リソースタイプと名前を抽出
        parts = resource_id.split("/")
        resource_name = parts[-1] if parts else ""

        # providers/Microsoft.xxx/resourceType/name の形式からタイプを抽出
        # 子リソースの場合も正しく抽出する（例: managedClusters/agentPools）
        resource_type = ""
        if "providers/" in resource_id:
            provider_idx = resource_id.find("providers/") + len("providers/")
            after_provider = resource_id[provider_idx:]
            type_parts = after_provider.split("/")
            # タイプセグメントのみを抽出（インデックス 0,1,3,5... = 名前をスキップ）
            # 例: Microsoft.ContainerService/managedClusters/aks-main/agentPools/system
            #     -> [Microsoft.ContainerService, managedClusters, aks-main, agentPools, system]
            #     -> Microsoft.ContainerService/managedClusters/agentPools
            if len(type_parts) >= 2:
                type_segments = [type_parts[0], type_parts[1]]  # Provider/ParentType
                # 子リソースタイプを追加（インデックス 3, 5, 7, ...）
                for i in range(3, len(type_parts), 2):
                    type_segments.append(type_parts[i])
                resource_type = "/".join(type_segments)

        # プロパティ変更をフラット化（リソースタイプを渡す）
        delta = change.get("delta", [])
        property_changes = flatten_property_changes(delta, "", bicep_dir, resource_type)

        changes.append(
            {
                "operation": change_type,
                "resourceId": resource_id,
                "resourceType": resource_type,
                "resourceName": resource_name,
                "propertyChanges": property_changes,
            }
        )

    return changes


def build_pending_evaluations(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """pending 状態の評価をリソースタイプ別に集計する。"""
    pending_count = 0
    by_resource_type: dict[str, int] = {}

    for change in changes:
        if change["operation"] not in ("Modify", "Delete"):
            continue

        resource_type = change["resourceType"]

        for prop_change in change.get("propertyChanges", []):
            eval_status = prop_change.get("evaluation", {}).get("status")
            if eval_status == "pending":
                pending_count += 1
                by_resource_type[resource_type] = (
                    by_resource_type.get(resource_type, 0) + 1
                )

    return {
        "count": pending_count,
        "byResourceType": by_resource_type,
    }


def build_output(
    what_if_result: dict[str, Any],
    template: str,
    location: str,
    bicep_dir: str = "./infra",
) -> dict[str, Any]:
    """出力 JSON を構築する。"""
    changes = extract_resource_changes(what_if_result, bicep_dir)

    # サマリー集計
    summary = {"create": 0, "modify": 0, "delete": 0, "noChange": 0, "ignore": 0}
    for change in changes:
        op = change["operation"].lower()
        if op == "create":
            summary["create"] += 1
        elif op == "modify":
            summary["modify"] += 1
        elif op == "delete":
            summary["delete"] += 1
        elif op == "nochange":
            summary["noChange"] += 1
        elif op == "ignore":
            summary["ignore"] += 1

    # 評価サマリー
    evaluation_summary = {"noise_confirmed": 0, "pending": 0, "drift_candidate": 0}
    for change in changes:
        for prop_change in change.get("propertyChanges", []):
            status = prop_change.get("evaluation", {}).get("status", "pending")
            if status in evaluation_summary:
                evaluation_summary[status] += 1

    # Bicep 照合サマリー
    bicep_summary = {"defined": 0, "notDefined": 0, "unknown": 0}
    for change in changes:
        for prop_change in change.get("propertyChanges", []):
            bicep_status = prop_change.get("bicepDefinition", {}).get(
                "status", "unknown"
            )
            if bicep_status in bicep_summary:
                bicep_summary[bicep_status] += 1

    # pending 評価の詳細
    pending_evaluations = build_pending_evaluations(changes)

    output = {
        "metadata": {
            "template": template,
            "location": location,
            "bicepDir": bicep_dir,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "summary": summary,
        "evaluationSummary": evaluation_summary,
        "bicepSummary": bicep_summary,
        "changes": changes,
        "pendingEvaluations": pending_evaluations,
    }

    # 注意メッセージ
    if bicep_summary["notDefined"] > 0:
        output["notice"] = (
            f"ℹ️ {bicep_summary['notDefined']} properties are not defined in Bicep files. "
            "Review the bicepDefinition.status='notDefined' items to determine if they "
            "represent drift (unintended changes) or noise (expected Azure-managed values)."
        )

    return output


class DisplayConfigLoader:
    """
    表示設定を JSON ファイルから読み込むクラス。

    設定は patterns/display_config.json で管理される。
    """

    def __init__(self, config_file: str | None = None) -> None:
        """
        設定ローダーを初期化する。

        Parameters:
            config_file: 設定 JSON ファイルのパス。
                         None の場合はスクリプトと同階層の patterns/display_config.json を使用。
        """
        if config_file is None:
            script_dir = Path(__file__).parent
            config_file = str(script_dir / "patterns" / "display_config.json")

        self._config_file = config_file
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        """設定ファイルを読み込む。"""
        if self._data is not None:
            return self._data

        try:
            with open(self._config_file, encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Failed to load display config file: %s", e)
            self._data = {"resource_type_display_names": {}, "filtered_resource_types": []}

        return self._data

    def get_display_names(self) -> dict[str, str]:
        """リソースタイプの表示名マッピングを取得する。"""
        return self._load().get("resource_type_display_names", {})

    def get_filtered_types(self) -> set[str]:
        """フィルタリングするリソースタイプのセットを取得する。"""
        return set(self._load().get("filtered_resource_types", []))


# グローバル設定ローダーインスタンス
_display_config_loader: DisplayConfigLoader | None = None


def get_display_config_loader(config_file: str | None = None) -> DisplayConfigLoader:
    """設定ローダーのインスタンスを取得する。"""
    global _display_config_loader
    if _display_config_loader is None:
        _display_config_loader = DisplayConfigLoader(config_file)
    return _display_config_loader


def get_resource_type_display_name(resource_type: str) -> str:
    """リソースタイプの表示名を取得する。"""
    loader = get_display_config_loader()
    display_names = loader.get_display_names()
    return display_names.get(resource_type, resource_type)


def is_main_resource(resource_type: str, resource_id: str) -> bool:
    """
    azd が表示する主要リソースかどうかを判定する。

    azd は子リソースや補助的なリソースをフィルタリングし、
    スコープレベルでデプロイされる主要リソースのみを表示する。

    判定基準:
    1. リソース ID の最後の /providers/ セグメント以降で子リソースを判定
       （拡張リソースの nested providers にも対応）
    2. display_config.json の filtered_resource_types でフィルタリング
    """
    # リソース ID からスコープを判定
    # 子リソースは /providers/Type/name/childType/childName のような形式
    # 拡張リソースは .../providers/Microsoft.Xxx/.../providers/Microsoft.Yyy/... の形式
    if resource_id:
        resource_id_lower = resource_id.lower()
        # 最後の /providers/ セグメントを基準にする（拡張リソース対応）
        # 拡張リソース例: .../managedClusters/aks-xxx/providers/Microsoft.Chaos/targets/xxx
        last_providers_idx = resource_id_lower.rfind("/providers/")
        if last_providers_idx >= 0:
            provider_path = resource_id[last_providers_idx + len("/providers/") :]
            segments = [s for s in provider_path.split("/") if s]
            # Microsoft.Xxx, resourceType, name の 3 つが基本
            # それ以上あれば子リソース（最後の provider 基準）
            if len(segments) > 3:
                return False

    # フィルタリングするリソースタイプを設定ファイルから取得
    loader = get_display_config_loader()
    filtered_types = loader.get_filtered_types()

    if resource_type.lower() in filtered_types:
        return False

    return True


def format_azd_style_output(output_data: dict[str, Any]) -> str:
    """
    azd provision --preview 風のテキスト出力を生成する。

    Args:
        output_data: build_output() の結果

    Returns:
        azd 風のフォーマット済みテキスト
    """
    lines: list[str] = []

    lines.append("Resources:")
    lines.append("")

    # 操作タイプの表示順と表示名
    operation_display = {
        "NoChange": "Skip",
        "Ignore": "Skip",
        "Modify": "Modify",
        "Create": "Create",
        "Delete": "Delete",
    }

    # 主要リソースのみフィルタリング
    main_resources = [
        c
        for c in output_data["changes"]
        if is_main_resource(c["resourceType"], c.get("resourceId", ""))
    ]

    # 最大幅を計算（整列用）
    max_op_len = 8  # "Modify" など
    max_type_len = 0
    for change in main_resources:
        display_type = get_resource_type_display_name(change["resourceType"])
        if len(display_type) > max_type_len:
            max_type_len = len(display_type)

    # 各リソースを出力
    for change in main_resources:
        op = change["operation"]
        display_op = operation_display.get(op) or op
        display_type = get_resource_type_display_name(change["resourceType"])
        resource_name = change["resourceName"]

        # 整列
        op_padded = display_op.ljust(max_op_len)
        type_padded = display_type.ljust(max_type_len)

        lines.append(f"  {op_padded} : {type_padded} : {resource_name}")

        # Skip 以外はプロパティ変更を表示
        if op not in ("NoChange", "Ignore") and change.get("propertyChanges"):
            for pc in change["propertyChanges"]:
                change_type = pc.get("changeType", "")
                path = pc.get("path", "")
                ref_info = pc.get("referenceInfo", "")

                # 変更タイプに応じた記号
                if change_type == "Delete":
                    symbol = "-"
                elif change_type == "Create":
                    symbol = "+"
                elif change_type == "Modify":
                    symbol = "~"
                else:
                    symbol = "*"

                # 参考情報がある場合は表示
                if ref_info:
                    lines.append(f"      {symbol} {path}  {ref_info}")
                else:
                    lines.append(f"      {symbol} {path}")

    return "\n".join(lines)


def main() -> int:
    """メイン関数。"""
    parser = argparse.ArgumentParser(
        description="Bicep what-if を実行し、変更点を JSON で出力する"
    )
    parser.add_argument(
        "-t",
        "--template",
        help="Bicep テンプレートファイル (デフォルト: ./infra/main.bicep)",
    )
    parser.add_argument("-l", "--location", help="Azure リージョン")
    parser.add_argument("-s", "--subscription", help="サブスクリプション ID")
    parser.add_argument(
        "--no-azd",
        action="store_true",
        help="azd 自動検出を無効化",
    )
    parser.add_argument(
        "-p",
        "--parameter",
        action="append",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help="パラメータ (例: -p environmentName dev)",
    )
    parser.add_argument(
        "-b",
        "--bicep-dir",
        default="./infra",
        help="Bicep ファイルのディレクトリ (デフォルト: ./infra)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "text"],
        default="text",
        help="出力フォーマット (デフォルト: text は azd 風, json)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="詳細なログを出力",
    )

    args = parser.parse_args()

    # ロギング設定
    setup_logging(verbose=args.verbose)

    # azd プロジェクト検出
    is_azd = False
    azd_values: dict[str, str] = {}
    if not args.no_azd:
        is_azd, azd_values = detect_azd_project()

    # パラメータ解決
    template = args.template
    location = args.location
    subscription = args.subscription
    parameters: dict[str, Any] = {}

    if is_azd:
        template = template or "./infra/main.bicep"
        location = location or azd_values.get("AZURE_LOCATION", "")
        subscription = subscription or azd_values.get("AZURE_SUBSCRIPTION_ID", "")

        # azd 環境変数をパラメータに変換
        # environment パラメータ（AZURE_ENV_NAME から）
        if "AZURE_ENV_NAME" in azd_values:
            parameters["environment"] = azd_values["AZURE_ENV_NAME"]
        if "AZURE_LOCATION" in azd_values:
            parameters["location"] = azd_values["AZURE_LOCATION"]
    else:
        template = template or "./main.bicep"

    # コマンドライン引数のパラメータを追加
    if args.parameter:
        for key, value in args.parameter:
            parameters[key] = value

    # 必須チェック
    if not template:
        logger.error("--template is required")
        return 1
    if not location:
        logger.error("--location is required")
        return 1
    if not os.path.exists(template):
        logger.error("Template file not found: %s", template)
        return 1

    # what-if 実行
    try:
        what_if_result = run_what_if(
            template=template,
            location=location,
            subscription=subscription,
            parameters=parameters if parameters else None,
        )
    except RuntimeError as e:
        logger.error("what-if failed: %s", e)
        return 1

    # 出力
    bicep_dir = args.bicep_dir
    output = build_output(what_if_result, template, location, bicep_dir)

    if args.format == "text":
        print(format_azd_style_output(output))
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))

    # パターン統計を保存
    loader = get_pattern_loader()
    loader.save_stats()

    # 未使用パターンの警告
    unused = loader.get_unused_patterns(days=30)
    if unused:
        logger.warning("=== 未使用パターン警告（30日以上マッチなし） ===")
        for item in unused[:5]:  # 最大5件表示
            logger.warning(
                "  %s: %s (最終マッチ: %d日前)",
                item["category"],
                item["pattern"],
                item["daysSinceLastMatch"],
            )
        if len(unused) > 5:
            logger.warning("  ... 他 %d 件", len(unused) - 5)

    return 0


if __name__ == "__main__":
    sys.exit(main())
