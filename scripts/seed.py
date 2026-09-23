"""Seed the database with sample listings. Populated once the listings model exists."""

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("No seed data yet.")


if __name__ == "__main__":
    main()
