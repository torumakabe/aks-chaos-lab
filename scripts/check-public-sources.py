#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from urllib.parse import urlparse

from approved_index_config import (
    LEGACY_INDEX_KEYS,
    PUBLIC_PACKAGE_HOSTS,
    UNSAFE_UV_ENVIRONMENT_VARIABLES,
    ApprovedIndexConfigError,
    user_uv_config_path,
)

POLICY_VARIABLE = "AZD_ALLOW_PUBLIC_SOURCES"
GUIDANCE = f"""
通常の azd up は public PyPI（API build / Functions remote build）、公開コンテナレジストリ、Helm repository を使用します。
公開取得が許可済みの場合:
  azd env set {POLICY_VARIABLE} true -e "<env>"
承認済み Python index を使う場合:
  uv run --no-project scripts/tasks.py package-api-approved-index
適用手順と対応範囲: docs/deployment.md#docker-build-のpackage-index
"""


def check_uv_sources(environ: Mapping[str, str]) -> None:
    overrides = sorted(
        name for name in UNSAFE_UV_ENVIRONMENT_VARIABLES if environ.get(name)
    )
    if overrides:
        raise ApprovedIndexConfigError(
            "通常の Docker build に反映されない uv 環境変数: " + ", ".join(overrides)
        )

    try:
        config_path = user_uv_config_path(environ)
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if environ.get("UV_CONFIG_FILE"):
            raise ApprovedIndexConfigError(
                "UV_CONFIG_FILE の指定先が存在しません。"
            ) from None
        return
    except OSError, ValueError:
        # 例外本文に含まれ得る認証情報を出力しない。
        raise ApprovedIndexConfigError(
            "user-level uv 設定を読み取れないか、TOML が不正です。"
        ) from None

    if LEGACY_INDEX_KEYS.intersection(config) or "pip" in config:
        raise ApprovedIndexConfigError(
            "user-level uv の取得元または検証設定は通常の Docker build に反映されません。"
        )
    indexes = config.get("index", [])
    if not isinstance(indexes, list):
        raise ApprovedIndexConfigError("user-level uv の index 設定が不正です。")
    for index in indexes:
        url = index.get("url") if isinstance(index, dict) else None
        if not isinstance(url, str):
            raise ApprovedIndexConfigError("user-level uv の index URL が不正です。")
        try:
            parsed = urlparse(url)
            public = (
                parsed.scheme == "https"
                and parsed.hostname in PUBLIC_PACKAGE_HOSTS
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            public = False
        if not public:
            raise ApprovedIndexConfigError(
                "user-level uv の index が public PyPI 以外、または不正です。"
            )


def main(environ: Mapping[str, str] | None = None) -> int:
    if environ is None:
        environ = os.environ
    reasons: list[str] = []
    policy = environ.get(POLICY_VARIABLE, "").strip().lower()
    if policy != "true":
        reasons.append(
            f"{POLICY_VARIABLE}=false（公開取得禁止）。"
            if policy == "false"
            else f"{POLICY_VARIABLE} が未設定または不正です（true / false を指定）。"
        )
    try:
        check_uv_sources(environ)
    except ApprovedIndexConfigError as error:
        reasons.append(str(error))
    if reasons:
        print("error: azd up を停止しました。", file=sys.stderr)
        for reason in reasons:
            print(f"- {reason}", file=sys.stderr)
        print(GUIDANCE, file=sys.stderr)
        return 1
    print("ok: 公開取得チェックを通過しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
