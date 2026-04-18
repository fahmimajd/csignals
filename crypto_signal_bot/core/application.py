"""Main application orchestrator."""

import asyncio
import logging
import signal as signal_module
from typing import Dict, List, Optional

from crypto_signal_bot.core.config import Config
from crypto_signal_bot.core.events import EventBus, EventType, Event
from crypto_signal_bot.infrastructure.binance_client import BinanceClient


logger = logging.getLogger(__name__)


class CryptoSignalApp:
    """
    Main application class that orchestrates all services.
    
    Responsibilities:
    - Service lifecycle management
    - Signal processing coordination
    - Graceful shutdown handling
    - Health monitoring
    """
    
    def __init__(self, config: Optional[Config] = None):
        """
        Initialize the application.
        
        Args:
            config: Application configuration (uses defaults if None)
        """
        self.config = config or Config.load()
        self.event_bus = EventBus()
        self.binance_client = BinanceClient(self.config.binance)
        
        self._services: Dict[str, object] = {}
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Setup signal handlers
        self._setup_signal_handlers()
        
        logger.info("CryptoSignalApp initialized")
    
    def _setup_signal_handlers(self) -> None:
        """Setup OS signal handlers for graceful shutdown."""
        def handle_signal(sig, frame):
            logger.info(f"Received signal {sig}, initiating shutdown...")
            asyncio.create_task(self.shutdown())
        
        try:
            signal_module.signal(signal_module.SIGINT, handle_signal)
            signal_module.signal(signal_module.SIGTERM, handle_signal)
            logger.info("Signal handlers registered")
        except ValueError:
            # Signal handlers only work in main thread
            logger.warning("Could not register signal handlers (not in main thread)")
    
    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing CryptoSignalApp...")
        
        # Initialize Binance client
        await self.binance_client.initialize()
        
        # Fetch and configure symbols
        symbols = await self._fetch_symbols()
        logger.info(f"Monitoring {len(symbols)} symbols: {symbols[:5]}...")
        
        # Initialize services (to be implemented)
        await self._initialize_services(symbols)
        
        # Subscribe to events
        self._subscribe_to_events()
        
        logger.info("CryptoSignalApp initialization complete")
    
    async def _fetch_symbols(self) -> List[str]:
        """Fetch list of symbols to monitor."""
        all_symbols = await self.binance_client.get_all_symbols()
        return self.config.symbols.get_symbols_list(all_symbols)
    
    async def _initialize_services(self, symbols: List[str]) -> None:
        """Initialize all monitoring services."""
        # Placeholder for service initialization
        # In full implementation, this would create and initialize:
        # - OrderBookMonitor
        # - LiquidationMonitor
        # - WhaleTradeMonitor
        # - OpenInterestTracker
        # - SignalAggregator
        # - SignalConfirmation
        # - ExitMonitor
        # - TrailingStopManager
        # - TelegramNotifier
        pass
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to important events."""
        self.event_bus.subscribe(EventType.SYSTEM_SHUTDOWN, self._on_shutdown_event)
    
    async def _on_shutdown_event(self, event: Event) -> None:
        """Handle shutdown event."""
        logger.info("Shutdown event received")
        await self.shutdown()
    
    async def run(self) -> None:
        """
        Run the main application loop.
        
        This method blocks until shutdown is requested.
        """
        if self._running:
            logger.warning("Application already running")
            return
        
        logger.info("Starting CryptoSignalApp...")
        self._running = True
        
        try:
            # Start all services
            await self._start_services()
            
            # Publish startup event
            await self.event_bus.publish_simple(
                EventType.SYSTEM_STARTUP,
                {"timestamp": asyncio.get_event_loop().time()},
                source="app",
            )
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        except Exception as e:
            logger.error(f"Application error: {e}")
            raise
        finally:
            await self.shutdown()
    
    async def _start_services(self) -> None:
        """Start all monitoring services."""
        # Placeholder for starting services
        pass
    
    async def shutdown(self) -> None:
        """
        Gracefully shutdown the application.
        
        Stops all services and closes connections.
        """
        if not self._running:
            return
        
        logger.info("Shutting down CryptoSignalApp...")
        self._running = False
        
        try:
            # Stop all services
            await self._stop_services()
            
            # Close Binance client
            await self.binance_client.close()
            
            # Publish shutdown event
            await self.event_bus.publish_simple(
                EventType.SYSTEM_SHUTDOWN,
                {"reason": "user_requested"},
                source="app",
            )
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            self._shutdown_event.set()
            logger.info("CryptoSignalApp shutdown complete")
    
    async def _stop_services(self) -> None:
        """Stop all monitoring services."""
        # Placeholder for stopping services
        for name, service in list(self._services.items()):
            try:
                if hasattr(service, 'stop'):
                    await service.stop()
                logger.info(f"Service '{name}' stopped")
            except Exception as e:
                logger.error(f"Error stopping service '{name}': {e}")
    
    def get_status(self) -> Dict:
        """Get application status information."""
        return {
            "running": self._running,
            "services_count": len(self._services),
            "event_subscribers": self.event_bus.get_all_subscriber_counts(),
        }
