"""
Three-level fallback chain for data source providers.
=====================================================
Provides FallbackRunner orchestration class for multi-provider fallback chains.

Degradation order per category:
  - market:      Tushare > baostock > AKShare > mootdx
  - fundamental: Tushare > AKShare
  - macro:       AKShare > Tushare
  - alt:         AKShare (sole source)

See docs/design/12-framework-refactor.md section 6.1

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Default degradation chains by metric category
DEFAULT_CHAINS = {
    # Market data
    "stock_price": ["tushare", "baostock", "akshare"],
    "index_daily": ["tushare", "baostock", "akshare"],
    "fund_nav": ["akshare", "tushare"],
    
    # Fundamental data
    "income_statement": ["tushare", "akshare"],
    "balance_sheet": ["tushare", "akshare"],
    "valuation": ["tushare", "akshare"],
    "dividend": ["tushare", "akshare"],
    
    # Macro data
    "macro_gdp": ["akshare", "tushare"],
    "macro_cpi": ["akshare", "tushare"],
    "macro_pmi": ["akshare", "tushare"],
    "macro_shibor": ["akshare", "tushare"],
    "macro_lpr": ["akshare", "tushare"],
    "macro_m1_m2": ["akshare", "tushare"],
    
    # Alternative data (AKShare is sole source)
    "stock_news": ["akshare"],
    "northbound_flow": ["tushare", "akshare"],
    "margin_detail": ["tushare", "akshare"],
    "block_trade": ["akshare"],
    
    # Fund data
    "fund_name": ["akshare"],
    "fund_rank": ["akshare"],
}


class FallbackRunner:
    """Orchestrates multi-provider fallback chain execution.
    
    Tries each provider in order until one succeeds.
    Logs fallback events with metrics (timing, errors).
    Returns data + source metadata.
    
    Example:
        runner = FallbackRunner(
            metric="stock_price",
            symbol="000001",
            start_date="20260501"
        )
        data, metadata = runner.fetch()
        # metadata = {"source": "tushare", "elapsed": 1.23, "attempts": 1}
    """

    def __init__(
        self,
        metric: str,
        chain: Optional[List[str]] = None,
        timeout_per_provider: float = 5.0,
        **kwargs: Any
    ) -> None:
        """Initialize FallbackRunner.
        
        Args:
            metric: Metric name (e.g. "stock_price", "macro_gdp")
            chain: Optional custom chain, otherwise uses DEFAULT_CHAINS
            timeout_per_provider: Seconds to wait per provider attempt
            **kwargs: Parameters to pass to each provider (symbol, start_date, etc.)
        """
        self.metric = metric
        self.kwargs = kwargs
        self.timeout_per_provider = timeout_per_provider
        
        # Use custom chain or default
        if chain:
            self.chain = chain
        else:
            self.chain = DEFAULT_CHAINS.get(metric, ["tushare", "akshare"])
        
        self.attempts: List[Dict[str, Any]] = []

    def fetch(self) -> Tuple[Optional[Any], Dict[str, Any]]:
        """Try fetching from each provider in chain, return first success.
        
        Returns:
            (data, metadata) where:
                data: DataFrame, dict, list, or None
                metadata: {
                    "source": provider name or "none" if all failed,
                    "elapsed": total time in seconds,
                    "attempts": number of providers tried,
                    "attempt_log": list of attempt details,
                    "error": error message if all failed
                }
        """
        t0 = time.time()

        for idx, provider_name in enumerate(self.chain, 1):
            attempt_result = self._try_provider(provider_name)
            self.attempts.append(attempt_result)

            if attempt_result["success"]:
                elapsed = time.time() - t0
                logger.info(
                    f"[FALLBACK] {self.metric}: {provider_name} succeeded after "
                    f"{attempt_result['elapsed']:.3f}s (attempt {idx}/{len(self.chain)})"
                )
                return attempt_result["data"], {
                    "source": provider_name,
                    "elapsed": round(elapsed, 3),
                    "attempts": idx,
                    "attempt_log": self.attempts,
                }

            else:
                logger.debug(
                    f"[FALLBACK] {self.metric}: {provider_name} failed "
                    f"({attempt_result['error'][:50]}), trying next..."
                )

        # All providers failed
        elapsed = time.time() - t0
        logger.warning(
            f"[FALLBACK] {self.metric}: all {len(self.chain)} providers exhausted "
            f"after {elapsed:.1f}s. Chain: {self.chain}"
        )

        return None, {
            "source": "none",
            "elapsed": round(elapsed, 3),
            "attempts": len(self.chain),
            "attempt_log": self.attempts,
            "error": "All providers in fallback chain failed",
        }

    def _try_provider(self, provider_name: str) -> Dict[str, Any]:
        """Try to fetch from a single provider.
        
        Returns dict with keys:
            - success: bool
            - data: result if successful, None if failed
            - elapsed: seconds taken
            - error: error message if failed
        """
        t0 = time.time()
        try:
            # Dynamically import and instantiate provider
            provider_instance = self._get_provider_instance(provider_name)
            if provider_instance is None:
                return {
                    "success": False,
                    "data": None,
                    "elapsed": time.time() - t0,
                    "error": f"Provider {provider_name} not available",
                }

            # Check provider availability
            if not provider_instance.is_available():
                return {
                    "success": False,
                    "data": None,
                    "elapsed": time.time() - t0,
                    "error": f"Provider {provider_name} is not available",
                }

            # Fetch data
            data = provider_instance.fetch(self.metric, **self.kwargs)

            if data is not None:
                return {
                    "success": True,
                    "data": data,
                    "elapsed": time.time() - t0,
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "data": None,
                    "elapsed": time.time() - t0,
                    "error": f"Provider {provider_name} returned None",
                }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "elapsed": time.time() - t0,
                "error": str(e),
            }

    def _get_provider_instance(self, provider_name: str) -> Optional[Any]:
        """Get provider instance by name.
        
        Lazily imports and instantiates provider adapters.
        Returns None if provider not found.
        """
        try:
            if provider_name == "tushare":
                from infra.data_source.providers.tushare_provider import TushareProvider
                return TushareProvider()
            
            elif provider_name == "baostock":
                from infra.data_source.providers.baostock_provider import BaostockProvider
                return BaostockProvider()
            
            elif provider_name == "akshare":
                from infra.data_source.providers.akshare_provider import AkshareProvider
                return AkshareProvider()
            
            elif provider_name == "tencent":
                from infra.data_source.providers.tencent_provider import TencentProvider
                return TencentProvider()
            
            elif provider_name == "mootdx":
                # mootdx is standalone functions, not provider adapter
                # For fallback chain, we'd need to wrap it as adapter
                logger.debug(f"mootdx fallback not yet supported (standalone functions)")
                return None
            
            else:
                logger.warning(f"Unknown provider: {provider_name}")
                return None

        except ImportError as e:
            logger.debug(f"Failed to import provider {provider_name}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Failed to instantiate provider {provider_name}: {e}")
            return None


def get_fallback_chain(metric: str) -> List[str]:
    """Get the default fallback chain for a metric.
    
    Args:
        metric: Metric name (e.g. "stock_price", "macro_gdp")
    
    Returns:
        List of provider names in fallback order, or ["akshare"] as default
    """
    return DEFAULT_CHAINS.get(metric, ["akshare"])


def fetch_with_fallback(
    metric: str,
    chain: Optional[List[str]] = None,
    **kwargs: Any
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Convenience function for fetching data with automatic fallback.
    
    Args:
        metric: Metric name to fetch
        chain: Optional custom fallback chain
        **kwargs: Parameters for the metric (symbol, start_date, etc.)
    
    Returns:
        (data, metadata) tuple from FallbackRunner.fetch()
    
    Example:
        data, meta = fetch_with_fallback(
            "stock_price",
            symbol="000001",
            start_date="20260501"
        )
        print(f"Got data from {meta['source']} in {meta['elapsed']}s")
    """
    runner = FallbackRunner(metric=metric, chain=chain, **kwargs)
    return runner.fetch()
