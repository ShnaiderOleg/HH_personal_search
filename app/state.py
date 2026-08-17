class _State:
    poller = None


state = _State()


def get_poller():
    return state.poller
