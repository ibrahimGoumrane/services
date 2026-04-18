"""Public API for the database seeding service package."""

from .main import seed_database, seed_single_url

__all__ = ["seed_database", "seed_single_url"]
