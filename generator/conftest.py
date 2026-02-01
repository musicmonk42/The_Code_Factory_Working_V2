    # Make the module itself callable and attribute-accessible
    def module_getattr(attr):
        # Handle special module attributes explicitly - RETURN them, don't raise
        if attr == '__spec__':
            return mock_module.__spec__
        elif attr == '__path__':
            return mock_module.__path__
        elif attr == '__file__':
            return mock_module.__file__
        elif attr == '__name__':
            return name
        elif attr == '__package__':
            return name.rpartition('.')[0] if '.' in name else ''
        elif attr == '__loader__':
            return None
        # Return MockCallable for everything else
        return MockCallable(f"{name}.{attr}")
