import resource

from app import main


def test_raise_fd_limit_lifts_soft_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
        main._raise_fd_limit(1024)
        assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] >= min(1024, hard)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
