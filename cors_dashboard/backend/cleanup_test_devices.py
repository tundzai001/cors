#!/usr/bin/env python3
"""
Cleanup script to delete test devices (LTPI prefix) in bulk
"""
import argparse
import asyncio
import logging
import sys
from typing import List, Optional

import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def get_all_devices(base_url: str, auth_token: Optional[str] = None) -> List[dict]:
    """Fetch all devices from backend"""
    headers = {}
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try to get devices list
            response = await client.get(f"{base_url}/api/devices", headers=headers)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Failed to fetch devices: {response.status_code}")
                return []
    except Exception as e:
        logger.error(f"Error fetching devices: {e}")
        return []


async def delete_device(base_url: str, serial: str, auth_token: Optional[str] = None) -> bool:
    """Delete a single device by serial"""
    headers = {}
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{base_url}/api/devices/{serial}",
                headers=headers
            )
            if response.status_code in [200, 204]:
                return True
            else:
                logger.warning(f"Failed to delete {serial}: {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"Error deleting {serial}: {e}")
        return False


async def cleanup_test_devices(
    base_url: str,
    prefix: str = "LTPI",
    auth_token: Optional[str] = None,
    dry_run: bool = False
) -> None:
    """Cleanup test devices with given prefix"""
    logger.info(f"Fetching all devices from {base_url}...")
    all_devices = await get_all_devices(base_url, auth_token)
    
    if not all_devices:
        logger.warning("No devices found or could not fetch devices. DB cleanup may be needed.")
        return
    
    # Filter test devices
    test_devices = [d for d in all_devices if d.get('serial', '').startswith(prefix)]
    
    if not test_devices:
        logger.info(f"No devices found with prefix '{prefix}'")
        return
    
    logger.info(f"Found {len(test_devices)} test devices with prefix '{prefix}':")
    for dev in test_devices[:10]:
        logger.info(f"  - {dev.get('serial')}")
    if len(test_devices) > 10:
        logger.info(f"  ... and {len(test_devices) - 10} more")
    
    if dry_run:
        logger.info("[DRY RUN] Would delete the above devices. Use --no-dry-run to actually delete.")
        return
    
    # Delete in parallel (but limit concurrency to avoid overwhelming server)
    logger.info(f"Deleting {len(test_devices)} test devices...")
    
    semaphore = asyncio.Semaphore(10)  # Max 10 concurrent delete requests
    
    async def delete_with_sem(serial: str) -> bool:
        async with semaphore:
            result = await delete_device(base_url, serial, auth_token)
            if result:
                logger.info(f"✓ Deleted {serial}")
            else:
                logger.warning(f"✗ Failed to delete {serial}")
            return result
    
    tasks = [delete_with_sem(d.get('serial')) for d in test_devices]
    results = await asyncio.gather(*tasks)
    
    deleted_count = sum(results)
    logger.info(f"Deleted {deleted_count}/{len(test_devices)} devices")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup test devices from CORS dashboard")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--prefix", default="LTPI", help="Device serial prefix to cleanup (default: LTPI)")
    parser.add_argument("--auth-token", help="Bearer token for authentication (if needed)")
    parser.add_argument("--no-dry-run", action="store_true", help="Actually delete devices (default is dry-run)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dry_run = not args.no_dry_run
    
    logger.info(f"Mode: {'DRY-RUN' if dry_run else 'ACTUAL DELETE'}")
    logger.info(f"Target: {args.base_url}")
    logger.info(f"Prefix: {args.prefix}")
    
    asyncio.run(
        cleanup_test_devices(
            base_url=args.base_url.rstrip("/"),
            prefix=args.prefix,
            auth_token=args.auth_token,
            dry_run=dry_run,
        )
    )


if __name__ == "__main__":
    main()
