class _State:
    poller = None
    telegram_reaction_poller = None


state = _State()


def get_poller():
    return state.poller
