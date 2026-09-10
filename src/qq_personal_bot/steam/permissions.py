GROUP_MANAGER_COMMANDS = frozenset(
    {
        "on",
        "off",
        "delid",
        "push_group",
        "delpush_group",
        "achievement_on",
        "achievement_off",
    }
)


def requires_group_manager(command: str) -> bool:
    return command.casefold() in GROUP_MANAGER_COMMANDS
