"""Configure a gateway from stdin, discover its models and optionally test the entire catalog."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from qq_personal_bot.ai_providers import provider_settings
from qq_personal_bot.core.ai_providers import normalize_ai_base_url
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.dsapi import DSAPIError, _request_chat_completion, fetch_dsapi_models
from qq_personal_bot.settings import AppSettings


def configure(settings, store, *, name, base_url, model, api_key, test_all=False, tls_mode="auto"):
    base_url = normalize_ai_base_url(base_url)
    existing = next(
        (
            item
            for item in store.list_ai_providers()
            if item["name"] == name and item["base_url"] == base_url
        ),
        None,
    )
    provider = {
        "id": existing["id"] if existing else 0,
        "base_url": base_url,
        "api_key": api_key,
        "default_model": model,
        "api_format": "openai",
        "tls_mode": tls_mode,
        "timeout_seconds": 60,
        "models": [],
    }
    routed = provider_settings(settings, provider)
    models = fetch_dsapi_models(routed)
    if not any(item["id"] == model for item in models):
        raise DSAPIError("The default model is absent from the gateway catalog")

    def probe(item):
        try:
            try:
                text = _request_chat_completion(
                    routed,
                    [{"role": "user", "content": "Reply with exactly OK."}],
                    model=item["id"],
                    max_tokens=128,
                )
            except DSAPIError:
                # Some reasoning routes need a larger budget even for this tiny prompt.
                text = _request_chat_completion(
                    routed,
                    [{"role": "user", "content": "Reply with exactly OK."}],
                    model=item["id"],
                    max_tokens=1024,
                )
            if not text:
                raise DSAPIError("empty text reply")
            item["test_status"] = "ok"
        except DSAPIError as exc:
            item["test_status"] = "error"
            item["test_error"] = str(exc)
        return item

    targets = models if test_all else [item for item in models if item["id"] == model]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(probe, item) for item in targets]
        for future in as_completed(futures):
            item = future.result()
            print(f"{item['id']}: {item['test_status']}", flush=True)
    default = next(item for item in models if item["id"] == model)
    if default.get("test_status") != "ok":
        raise DSAPIError("Default model test failed; the existing active provider was kept")
    provider_id = store.save_ai_provider(
        name=name,
        base_url=base_url,
        default_model=model,
        api_key=api_key,
        api_format="openai",
        tls_mode=tls_mode,
        timeout_seconds=60,
        provider_id=existing["id"] if existing else None,
    )
    provider = store.get_ai_provider(provider_id)
    store.save_ai_provider_models(provider_id, models, expected_updated_at=provider["updated_at"])
    store.activate_ai_provider(provider_id)
    print(f"Active provider: {name}; default model: {model}; configured models: {len(models)}")
    return provider_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--test-all", action="store_true")
    parser.add_argument("--tls-mode", choices=("auto", "tls12"), default="auto")
    args = parser.parse_args()
    key = sys.stdin.readline().strip()
    if not key:
        parser.error("Pass the API key through stdin")
    settings = AppSettings.from_env()
    settings.validate()
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    try:
        configure(
            settings,
            store,
            name=args.name,
            base_url=args.base_url,
            model=args.model,
            api_key=key,
            test_all=args.test_all,
            tls_mode=args.tls_mode,
        )
    except (ValueError, DSAPIError) as exc:
        print(f"Configuration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
