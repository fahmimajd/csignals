"""Crypto Signal Bot - Main Entry Point (Refactored)"""

import asyncio
import logging
import sys

from crypto_signal_bot.utils.logging import setup_logging
from crypto_signal_bot.core.application import CryptoSignalApp
from crypto_signal_bot.core.config import Config


logger = logging.getLogger(__name__)


async def main():
    """Main entry point for the refactored application."""
    # Setup logging
    setup_logging()
    
    logger.info("Starting Crypto Signal Bot (Refactored)...")
    
    try:
        # Load configuration
        config = Config.load()
        logger.info("Configuration loaded successfully")
        
        # Create and initialize application
        app = CryptoSignalApp(config)
        await app.initialize()
        
        # Run main loop
        await app.run()
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Application shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
