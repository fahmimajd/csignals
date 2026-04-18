"""Event bus implementation for loose coupling between modules."""

import asyncio
import logging
from typing import Callable, Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Standard event types in the system."""
    
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_CONFIRMED = "signal_confirmed"
    SIGNAL_CANCELLED = "signal_cancelled"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    TRAILING_STOP_UPDATED = "trailing_stop_updated"
    PRICE_UPDATE = "price_update"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    LIQUIDATION_DETECTED = "liquidation_detected"
    WHALE_ACTIVITY = "whale_activity"
    OI_CHANGE = "oi_change"
    FUNDING_RATE_CHANGE = "funding_rate_change"
    ERROR = "error"
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"


@dataclass
class Event:
    """Represents an event in the system."""
    
    event_type: EventType
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event_type": self.event_type.value,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }


class EventBus:
    """
    Central event bus for publishing and subscribing to events.
    
    Implements pub/sub pattern for loose coupling between modules.
    All event handlers are called asynchronously.
    """
    
    _instance: Optional["EventBus"] = None
    
    def __new__(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._lock = asyncio.Lock()
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._processing = False
        self._initialized = True
        logger.info("EventBus initialized")
    
    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """
        Subscribe to an event type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Async function to call when event occurs
        """
        if not asyncio.iscoroutinefunction(handler):
            logger.warning(f"Handler for {event_type} is not async, wrapping it")
            original_handler = handler
            async def async_wrapper(*args, **kwargs):
                return original_handler(*args, **kwargs)
            handler = async_wrapper
        
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed handler to {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, handler: Callable) -> None:
        """Unsubscribe from an event type."""
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(handler)
                logger.debug(f"Unsubscribed handler from {event_type.value}")
            except ValueError:
                pass
    
    async def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        await self._event_queue.put(event)
        
        if not self._processing:
            asyncio.create_task(self._process_events())
    
    async def publish_simple(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        source: str = ""
    ) -> None:
        """
        Convenience method to publish a simple event.
        
        Args:
            event_type: Type of event
            payload: Event payload
            source: Source module name
        """
        event = Event(
            event_type=event_type,
            payload=payload,
            source=source,
        )
        await self.publish(event)
    
    async def _process_events(self) -> None:
        """Process events from the queue."""
        self._processing = True
        
        try:
            while True:
                try:
                    event = self._event_queue.get_nowait()
                    await self._dispatch_event(event)
                    self._event_queue.task_done()
                except asyncio.QueueEmpty:
                    break
        finally:
            self._processing = False
    
    async def _dispatch_event(self, event: Event) -> None:
        """Dispatch event to all subscribers."""
        subscribers = self._subscribers.get(event.event_type, [])
        
        if not subscribers:
            logger.debug(f"No subscribers for {event.event_type.value}")
            return
        
        logger.debug(
            f"Dispatching {event.event_type.value} to {len(subscribers)} handlers"
        )
        
        # Call all handlers concurrently
        tasks = []
        for handler in subscribers:
            try:
                task = asyncio.create_task(handler(event))
                tasks.append(task)
            except Exception as e:
                logger.error(f"Error creating task for {event.event_type.value}: {e}")
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Handler {i} for {event.event_type.value} failed: {result}"
                    )
    
    def get_subscriber_count(self, event_type: EventType) -> int:
        """Get number of subscribers for an event type."""
        return len(self._subscribers.get(event_type, []))
    
    def get_all_subscriber_counts(self) -> Dict[str, int]:
        """Get subscriber counts for all event types."""
        return {
            event_type.value: len(handlers)
            for event_type, handlers in self._subscribers.items()
        }


# Global event bus instance
event_bus = EventBus()
