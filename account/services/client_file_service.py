def open_client_file(field):
    if not field:
        raise FileNotFoundError

    try:
        return field.open("rb")
    except FileNotFoundError:
        restore = getattr(field.storage, "fetch_remote_to_local", None)
        if not callable(restore) or not restore(field.name):
            raise
    return field.open("rb")
