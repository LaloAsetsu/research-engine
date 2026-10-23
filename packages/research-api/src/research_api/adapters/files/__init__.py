"""Files adapter: folders and files that only the service's own account may read."""

from .bench import BenchFileStore
from .private import PrivacyError, create_private_file, ensure_private_dir, ensure_private_file
from .sets import SetFileStore

__all__ = [
    "BenchFileStore",
    "PrivacyError",
    "SetFileStore",
    "create_private_file",
    "ensure_private_dir",
    "ensure_private_file",
]
