"""Mount security module for Pyldon.

Migrated from NanoClaw src/mount-security.ts.

Validates additional mounts against an allowlist stored OUTSIDE the project root.
This prevents container agents from modifying security configuration.

Allowlist location: ~/.config/pyldon/mount-allowlist.json
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from pyldon.config import MOUNT_ALLOWLIST_PATH
from pyldon.models import (
    AdditionalMount,
    AllowedRoot,
    MountAllowlist,
    MountValidationResult,
)

# Cache the allowlist in memory - only reloads on process restart
_cached_allowlist: MountAllowlist | None = None
_allowlist_load_error: str | None = None

# Default blocked patterns - paths that should never be mounted
DEFAULT_BLOCKED_PATTERNS = [
    ".ssh",
    ".gnupg",
    ".gpg",
    ".aws",
    ".azure",
    ".gcloud",
    ".kube",
    ".docker",
    "credentials",
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "private_key",
    ".secret",
]


def load_mount_allowlist() -> MountAllowlist | None:
    """Load the mount allowlist from the external config location.

    Returns None if the file doesn't exist or is invalid.
    Result is cached in memory for the lifetime of the process.
    """
    global _cached_allowlist, _allowlist_load_error

    if _cached_allowlist is not None:
        return _cached_allowlist

    if _allowlist_load_error is not None:
        return None

    try:
        if not MOUNT_ALLOWLIST_PATH.exists():
            _allowlist_load_error = f"Mount allowlist not found at {MOUNT_ALLOWLIST_PATH}"
            logger.warning(
                "Mount allowlist not found at {} - additional mounts will be BLOCKED. "
                "Create the file to enable additional mounts.",
                MOUNT_ALLOWLIST_PATH,
            )
            return None

        content = MOUNT_ALLOWLIST_PATH.read_text(encoding="utf-8")
        data = json.loads(content)
        allowlist = MountAllowlist(**data)

        # Merge with default blocked patterns
        merged = list(set(DEFAULT_BLOCKED_PATTERNS + allowlist.blocked_patterns))
        allowlist.blocked_patterns = merged

        _cached_allowlist = allowlist
        logger.info(
            "Mount allowlist loaded: path={}, allowed_roots={}, blocked_patterns={}",
            MOUNT_ALLOWLIST_PATH,
            len(allowlist.allowed_roots),
            len(allowlist.blocked_patterns),
        )

        return _cached_allowlist

    except Exception as e:
        _allowlist_load_error = str(e)
        logger.error(
            "Failed to load mount allowlist at {} - additional mounts will be BLOCKED: {}",
            MOUNT_ALLOWLIST_PATH,
            e,
        )
        return None


def _expand_path(p: str) -> Path:
    """Expand ~ to home directory and resolve to absolute path."""
    return Path(p).expanduser().resolve()


def _get_real_path(p: Path) -> Path | None:
    """Get the real path, resolving symlinks. Returns None if path doesn't exist."""
    try:
        return p.resolve(strict=True)
    except OSError:
        return None


def _matches_blocked_pattern(real_path: Path, blocked_patterns: list[str]) -> str | None:
    """Check if a path matches any blocked pattern."""
    path_str = str(real_path)
    path_parts = real_path.parts

    for pattern in blocked_patterns:
        # Check if any path component matches the pattern
        for part in path_parts:
            if part == pattern or pattern in part:
                return pattern
        # Also check if the full path contains the pattern
        if pattern in path_str:
            return pattern

    return None


def _find_allowed_root(
    real_path: Path, allowed_roots: list[AllowedRoot]
) -> AllowedRoot | None:
    """Check if a real path is under an allowed root."""
    for root in allowed_roots:
        expanded_root = _expand_path(root.path)
        real_root = _get_real_path(expanded_root)

        if real_root is None:
            continue

        # Check if real_path is under real_root
        try:
            real_path.relative_to(real_root)
            return root
        except ValueError:
            continue

    return None


def _is_valid_container_path(container_path: str) -> bool:
    """Validate the container path to prevent escaping /workspace/extra/."""
    if ".." in container_path:
        return False
    if container_path.startswith("/"):
        return False
    if not container_path or not container_path.strip():
        return False
    return True


def validate_mount(mount: AdditionalMount, is_main: bool) -> MountValidationResult:
    """Validate a single additional mount against the allowlist."""
    allowlist = load_mount_allowlist()

    if allowlist is None:
        return MountValidationResult(
            allowed=False,
            reason=f"No mount allowlist configured at {MOUNT_ALLOWLIST_PATH}",
        )

    # Validate container path first (cheap check)
    if not _is_valid_container_path(mount.container_path):
        return MountValidationResult(
            allowed=False,
            reason=f'Invalid container path: "{mount.container_path}" - must be relative, non-empty, and not contain ".."',
        )

    # Expand and resolve the host path
    expanded_path = _expand_path(mount.host_path)
    real_path = _get_real_path(expanded_path)

    if real_path is None:
        return MountValidationResult(
            allowed=False,
            reason=f'Host path does not exist: "{mount.host_path}" (expanded: "{expanded_path}")',
        )

    # Check against blocked patterns
    blocked_match = _matches_blocked_pattern(real_path, allowlist.blocked_patterns)
    if blocked_match is not None:
        return MountValidationResult(
            allowed=False,
            reason=f'Path matches blocked pattern "{blocked_match}": "{real_path}"',
        )

    # Check if under an allowed root
    allowed_root = _find_allowed_root(real_path, allowlist.allowed_roots)
    if allowed_root is None:
        allowed_paths = ", ".join(str(_expand_path(r.path)) for r in allowlist.allowed_roots)
        return MountValidationResult(
            allowed=False,
            reason=f'Path "{real_path}" is not under any allowed root. Allowed roots: {allowed_paths}',
        )

    # Determine effective readonly status
    requested_read_write = mount.readonly is False
    effective_readonly = True  # Default to readonly

    if requested_read_write:
        if not is_main and allowlist.non_main_read_only:
            logger.info("Mount forced to read-only for non-main group: {}", mount.host_path)
        elif not allowed_root.allow_read_write:
            logger.info(
                "Mount forced to read-only - root does not allow read-write: {} (root: {})",
                mount.host_path, allowed_root.path,
            )
        else:
            effective_readonly = False

    desc = f' ({allowed_root.description})' if allowed_root.description else ""
    return MountValidationResult(
        allowed=True,
        reason=f'Allowed under root "{allowed_root.path}"{desc}',
        real_host_path=str(real_path),
        effective_readonly=effective_readonly,
    )


def validate_additional_mounts(
    mounts: list[AdditionalMount],
    group_name: str,
    is_main: bool,
) -> list[dict[str, str | bool]]:
    """Validate all additional mounts for a group.

    Returns list of validated mounts (only those that passed validation).
    Logs warnings for rejected mounts.
    """
    validated: list[dict[str, str | bool]] = []

    for mount in mounts:
        result = validate_mount(mount, is_main)

        if result.allowed:
            validated.append({
                "host_path": result.real_host_path or "",
                "container_path": f"/workspace/extra/{mount.container_path}",
                "readonly": result.effective_readonly or True,
            })
            logger.debug(
                "Mount validated: group={}, host={}, container={}, readonly={}, reason={}",
                group_name, result.real_host_path, mount.container_path,
                result.effective_readonly, result.reason,
            )
        else:
            logger.warning(
                "Additional mount REJECTED: group={}, path={}, container={}, reason={}",
                group_name, mount.host_path, mount.container_path, result.reason,
            )

    return validated


def generate_allowlist_template() -> str:
    """Generate a template allowlist file for users to customize."""
    template = MountAllowlist(
        allowed_roots=[
            AllowedRoot(
                path="~/projects",
                allow_read_write=True,
                description="Development projects",
            ),
            AllowedRoot(
                path="~/repos",
                allow_read_write=True,
                description="Git repositories",
            ),
            AllowedRoot(
                path="~/Documents/work",
                allow_read_write=False,
                description="Work documents (read-only)",
            ),
        ],
        blocked_patterns=["password", "secret", "token"],
        non_main_read_only=True,
    )
    return json.dumps(template.model_dump(), indent=2)
