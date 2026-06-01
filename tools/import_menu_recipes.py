from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def parse_args() -> argparse.Namespace:
    from qq_personal_bot.settings import AppSettings

    settings = AppSettings.from_env()
    parser = argparse.ArgumentParser(description="Import local Chinese menu recipes into SQLite.")
    parser.add_argument("--db", default=str(settings.db_path), help="SQLite database path")
    parser.add_argument(
        "--seed",
        default=str(settings.menu_seed_path),
        help="JSONL seed file path",
    )
    parser.add_argument(
        "--image-dir",
        default=str(settings.menu_image_dir),
        help="Directory used to cache local menu images",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Import only local JSONL seed data and skip remote HowToCook recipes",
    )
    parser.add_argument(
        "--howtocook-only",
        action="store_true",
        help="Import only remote HowToCook recipes and skip local JSONL seed data",
    )
    parser.add_argument(
        "--howtocook-limit",
        type=int,
        default=None,
        help="Optional limit for HowToCook recipe imports",
    )
    parser.add_argument(
        "--purge-legacy-cache",
        action="store_true",
        help="Delete old 今日菜单 cache namespaces before importing",
    )
    return parser.parse_args()


def main() -> int:
    from qq_personal_bot.core.store import PolicyStore
    from qq_personal_bot.settings import AppSettings

    args = parse_args()
    settings = AppSettings.from_env(
        {
            "QQBOT_DB_PATH": args.db,
            "QQBOT_MENU_SEED_PATH": args.seed,
            "QQBOT_MENU_IMAGE_DIR": args.image_dir,
        }
    )
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    if args.purge_legacy_cache:
        purged = store.purge_legacy_menu_caches()
        print(f"Purged {purged} legacy 今日菜单 cache rows")
    if args.seed_only and args.howtocook_only:
        print("--seed-only and --howtocook-only cannot be used together", file=sys.stderr)
        return 2

    seed_imported = 0
    if not args.howtocook_only:
        seed_imported = store.import_menu_recipes(settings.menu_seed_path, settings.menu_image_dir)
    howtocook_imported = 0
    if not args.seed_only:
        howtocook_imported = store.import_howtocook_recipes(
            settings.menu_image_dir,
            limit=args.howtocook_limit,
        )
    print(
        f"Imported {seed_imported + howtocook_imported} menu recipes into {settings.db_path} "
        f"(seed={seed_imported}, howtocook={howtocook_imported})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
