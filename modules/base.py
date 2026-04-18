"""
Base classes for crypto signal monitors.
Provides shared Binance client management and common monitor patterns.
"""
import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Optional

from binance import AsyncClient, BinanceSocketManager
from binance.exceptions import BinanceWebsocketUnableToConnect, BinanceWebsocketQueueOverflow
import config

logger = logging.getLogger(__name__)

# recv() timeout - prevents hanging forever if WebSocket goes silent
WEBSOCKET_RECV_TIMEOUT = 60  # seconds

# Reconnect settings
MAX_RECONNECT_DELAY = 60  # seconds
BASE_RECONNECT_DELAY = 1  # seconds
MAX_CONSECUTIVE_ERRORS = 10  # give up after this many errors in a row


class BinanceClientManager:
    """
    Singleton manager for shared Binance API client and WebSocket manager.
    Prevents creating multiple client instances.
    Thread-safe via instance-level lock created in __new__.
    """
    _instance: Optional['BinanceClientManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Instance-level lock — safe across multiple event loops
            cls._instance._lock = asyncio.Lock()
            # Socket creation lock — serializes multiplex socket creation
            # to prevent BinanceSocketManager queue overflow when many
            # batch tasks try to connect simultaneously.
            cls._instance._socket_lock = asyncio.Lock()
            cls._instance._client = None
            cls._instance._bm = None
        return cls._instance

    async def get_client(self) -> AsyncClient:
        """Get or create the shared AsyncClient instance."""
        if self._client is None:
            async with self._lock:
                # Double-check after acquiring lock
                if self._client is None:
                    self._client = await AsyncClient.create(
                        api_key=config.BINANCE_API_KEY,
                        api_secret=config.BINANCE_API_SECRET,
                        testnet=config.BINANCE_TESTNET
                    )
                    logger.info("Binance AsyncClient created (shared)")
        return self._client

    async def get_socket_manager(self) -> BinanceSocketManager:
        """Get or create the shared SocketManager instance."""
        if self._bm is None:
            client = await self.get_client()
            self._bm = BinanceSocketManager(client)
        return self._bm

    async def create_multiplex_socket(self, streams: list):
        """
        Create a multiplex socket, serialized via _socket_lock to prevent
        queue overflow when multiple batch tasks connect simultaneously.
        """
        async with self._socket_lock:
            bm = await self.get_socket_manager()
            return bm.futures_multiplex_socket(streams)

    async def close(self):
        """Close the shared client connection."""
        async with self._lock:
            if self._client:
                await self._client.close_connection()
                self._client = None
                self._bm = None
                logger.info("Binance AsyncClient closed (shared)")

    def is_closed(self) -> bool:
        """Check if the client has been closed."""
        return self._client is None


class Monitor(ABC):
    """
    Abstract base class for all monitors.
    Provides common lifecycle management and error handling patterns.
    """
    _client_manager: BinanceClientManager = None

    def __init__(self):
        self.client: Optional[AsyncClient] = None
        self.bm: Optional[BinanceSocketManager] = None
        self.running: bool = False
        self._tasks: list = []

    @classmethod
    def get_client_manager(cls) -> BinanceClientManager:
        """Get the shared client manager instance."""
        if cls._client_manager is None:
            cls._client_manager = BinanceClientManager()
        return cls._client_manager

    async def initialize(self):
        """Initialize the monitor - must be called before start()."""
        cm = self.get_client_manager()
        self.client = await cm.get_client()
        self.bm = await cm.get_socket_manager()
        await self._on_initialize()
        logger.info(f"{self.__class__.__name__} initialized")

    @abstractmethod
    async def _on_initialize(self):
        """Subclass-specific initialization logic."""
        pass

    async def start(self):
        """Start the monitor's background tasks."""
        self.running = True
        await self._on_start()
        logger.info(f"{self.__class__.__name__} started")

    @abstractmethod
    async def _on_start(self):
        """Subclass-specific start logic (e.g., subscribe to streams)."""
        pass

    async def stop(self):
        """Stop the monitor and cancel all background tasks."""
        self.running = False

        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            # Gather with return_exceptions to avoid propagating CancelledError
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            for task, result in zip(self._tasks, results):
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    logger.warning(
                        f"Task {task.get_name()} raised during shutdown: {result}"
                    )
        self._tasks.clear()

        await self._on_stop()
        logger.info(f"{self.__class__.__name__} stopped")

    @abstractmethod
    async def _on_stop(self):
        """Subclass-specific cleanup logic."""
        pass

    def _create_task(self, coro) -> asyncio.Task:
        """Helper to create and track background tasks with exception logging."""
        task = asyncio.create_task(coro)
        task.add_done_callback(self._task_done_callback)
        self._tasks.append(task)
        return task

    @staticmethod
    def _task_done_callback(task: asyncio.Task):
        """Log unhandled exceptions from background tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(
                f"Unhandled exception in task {task.get_name()}: {exc}",
                exc_info=exc
            )

    async def _ensure_client_alive(self):
        """
        Ensure the shared client/socket_manager are alive.
        Recreates them if they were closed (e.g., by a reset).
        """
        cm = self.get_client_manager()
        if cm.is_closed():
            logger.info(f"{self.__class__.__name__}: Re-initializing closed client...")
            self.client = await cm.get_client()
            self.bm = await cm.get_socket_manager()

    @staticmethod
    def _jitter_delay(base_delay: float) -> float:
        """
        Add random jitter to delay to prevent thundering herd.
        Returns delay with ±25% random jitter.
        """
        jitter = base_delay * 0.25 * (random.random() * 2 - 1)  # ±25%
        return max(0.1, base_delay + jitter)

    @staticmethod
    def _calculate_backoff(attempt: int, base_delay: float = BASE_RECONNECT_DELAY,
                          max_delay: float = MAX_RECONNECT_DELAY) -> float:
        """
        Calculate exponential backoff with jitter for reconnection attempts.
        Uses full jitter strategy: uniform random between 0 and exponential cap.
        """
        cap = min(base_delay * (2 ** attempt), max_delay)
        return random.uniform(0, cap)


def format_price(price: float, decimals: int = 2) -> str:
    """Format price with thousand separators."""
    if price >= 1000:
        return f"{price:,.{decimals}f}"
    elif price >= 1:
        return f"{price:,.{decimals}f}"
    else:
        return f"{price:,.{decimals}f}"


def format_volume(volume: float) -> str:
    """Format large volume numbers with K/M/B suffixes."""
    if volume >= 1_000_000_000:
        return f"{volume / 1_000_000_000:.1f}B"
    elif volume >= 1_000_000:
        return f"{volume / 1_000_000:.1f}M"
    elif volume >= 1_000:
        return f"{volume / 1_000:.1f}K"
    return f"{volume:.0f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """Format percentage value."""
    return f"{value:+.{decimals}f}%"
