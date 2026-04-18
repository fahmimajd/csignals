"""Abstract base class for all services."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from crypto_signal_bot.core.events import EventBus
    from crypto_signal_bot.infrastructure.binance_client import BinanceClient


logger = logging.getLogger(__name__)


class BaseService(ABC):
    """
    Abstract base class for all services in the crypto signal bot.
    
    Provides common functionality like lifecycle management, error handling,
    and access to shared resources (event bus, binance client).
    """
    
    def __init__(
        self,
        name: str,
        event_bus: Optional["EventBus"] = None,
        binance_client: Optional["BinanceClient"] = None,
    ):
        """
        Initialize base service.
        
        Args:
            name: Service name for logging
            event_bus: Shared event bus instance
            binance_client: Shared Binance client instance
        """
        self.name = name
        self._event_bus = event_bus
        self._binance_client = binance_client
        self._initialized = False
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        logger.info(f"{name} service created")
    
    @property
    def event_bus(self) -> "EventBus":
        """Get event bus instance."""
        if self._event_bus is None:
            from crypto_signal_bot.core.events import event_bus
            return event_bus
        return self._event_bus
    
    @property
    def binance_client(self) -> "BinanceClient":
        """Get Binance client instance."""
        if self._binance_client is None:
            raise RuntimeError(
                f"{self.name} service requires a Binance client. "
                "Please inject one during initialization."
            )
        return self._binance_client
    
    async def initialize(self) -> None:
        """
        Initialize the service.
        
        Override this method to add custom initialization logic.
        """
        if self._initialized:
            logger.warning(f"{self.name} service already initialized")
            return
        
        logger.info(f"Initializing {self.name} service...")
        try:
            await self._on_initialize()
            self._initialized = True
            logger.info(f"{self.name} service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize {self.name} service: {e}")
            raise
    
    @abstractmethod
    async def _on_initialize(self) -> None:
        """
        Custom initialization logic.
        
        Override this method in subclasses.
        """
        pass
    
    async def start(self) -> None:
        """
        Start the service.
        
        Override this method to add custom start logic.
        """
        if not self._initialized:
            raise RuntimeError(f"{self.name} service not initialized")
        
        if self._running:
            logger.warning(f"{self.name} service already running")
            return
        
        logger.info(f"Starting {self.name} service...")
        try:
            self._running = True
            await self._on_start()
            logger.info(f"{self.name} service started successfully")
        except Exception as e:
            logger.error(f"Failed to start {self.name} service: {e}")
            self._running = False
            raise
    
    @abstractmethod
    async def _on_start(self) -> None:
        """
        Custom start logic.
        
        Override this method in subclasses.
        """
        pass
    
    async def stop(self) -> None:
        """
        Stop the service gracefully.
        
        Cancels all running tasks and performs cleanup.
        """
        if not self._running:
            logger.debug(f"{self.name} service not running")
            return
        
        logger.info(f"Stopping {self.name} service...")
        
        try:
            # Cancel all tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            
            # Custom stop logic
            await self._on_stop()
            
            self._running = False
            self._tasks.clear()
            logger.info(f"{self.name} service stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping {self.name} service: {e}")
            raise
    
    @abstractmethod
    async def _on_stop(self) -> None:
        """
        Custom stop logic.
        
        Override this method in subclasses.
        """
        pass
    
    def create_task(self, coro) -> asyncio.Task:
        """
        Create and track an asyncio task.
        
        Tasks are automatically cancelled when the service stops.
        
        Args:
            coro: Coroutine to run
            
        Returns:
            Created task
        """
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)
        return task
    
    async def execute_with_retry(
        self,
        coro_func,
        max_retries: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
    ) -> Any:
        """
        Execute a coroutine with retry logic.
        
        Args:
            coro_func: Async function to execute
            max_retries: Maximum number of retries
            delay: Initial delay between retries
            backoff: Backoff multiplier
            
        Returns:
            Result of the coroutine
            
        Raises:
            Last exception if all retries fail
        """
        current_delay = delay
        
        for attempt in range(max_retries):
            try:
                return await coro_func()
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(
                        f"{self.name}: Operation failed after {max_retries} attempts: {e}"
                    )
                    raise
                
                logger.warning(
                    f"{self.name}: Attempt {attempt + 1} failed: {e}. "
                    f"Retrying in {current_delay}s..."
                )
                await asyncio.sleep(current_delay)
                current_delay *= backoff
        
        # Should never reach here
        raise RuntimeError("Retry logic error")
    
    @property
    def is_initialized(self) -> bool:
        """Check if service is initialized."""
        return self._initialized
    
    @property
    def is_running(self) -> bool:
        """Check if service is running."""
        return self._running
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status information."""
        return {
            "name": self.name,
            "initialized": self._initialized,
            "running": self._running,
            "active_tasks": len(self._tasks),
        }
