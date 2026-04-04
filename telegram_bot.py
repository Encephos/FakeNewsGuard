"""Backward-compat entry point – delegates to the bot/ package.

docker-compose.yml uses `python telegram_bot.py` as the command, so this
file must remain at the root level.
"""

from bot.main import main

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
