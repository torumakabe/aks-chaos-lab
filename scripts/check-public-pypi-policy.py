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
from public_pypi_policy import (
    DISALLOW_PUBLIC_PYPI_VARIABLE,
    PublicPypiPolicyError,
    public_pypi_disallowed,
)

GUIDANCE = f"""
この設定が対象とするのは、API build と Functions publisher が使う public PyPI です。コンテナイメージの registry と Helm repository は対象外です。
標準の azd up が使う public PyPI を禁止する場合:
  azd env set {DISALLOW_PUBLIC_PYPI_VARIABLE} true -e "<env>"
承認済み Python index を使う場合:
  uv run --no-project scripts/tasks.py package-api-approved-index
適用手順と対応範囲: docs/deployment.md#docker-build-のpackage-index
"""


def check_pypi_configuration(environ: Mapping[str, str]) -> None:
    overrides = sorted(
        name for name in UNSAFE_UV_ENVIRONMENT_VARIABLES if environ.get(name)
    )
    if overrides:
        raise ApprovedIndexConfigError(
            "API の Docker build に反映されない uv 環境変数: " + ", ".join(overrides)
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
    try:
        if public_pypi_disallowed(environ):
            reasons.append(
                f"{DISALLOW_PUBLIC_PYPI_VARIABLE}=true（public PyPI を禁止しています）。"
            )
    except PublicPypiPolicyError as error:
        reasons.append(str(error))
    try:
        check_pypi_configuration(environ)
    except ApprovedIndexConfigError as error:
        reasons.append(str(error))
    if reasons:
        print("error: azd up を停止しました。", file=sys.stderr)
        for reason in reasons:
            print(f"- {reason}", file=sys.stderr)
        print(GUIDANCE, file=sys.stderr)
        return 1
    print("ok: public PyPI 設定チェックを通過しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
