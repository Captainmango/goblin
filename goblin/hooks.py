HOOK_REGISTRY = {}

def hook(name):
    def decorator(fn):
        HOOK_REGISTRY[name] = fn
        return fn
    return decorator
