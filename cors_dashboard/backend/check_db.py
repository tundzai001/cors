import asyncio
from sqlalchemy import select
from app.database import device_session_scope
from app.models import Device

async def main():
    async with device_session_scope() as session:
        res = await session.execute(select(Device))
        devices = res.scalars().all()
        print(f"Total devices: {len(devices)}")
        for d in devices:
            print("-" * 50)
            print(f"Serial: {d.serial}")
            print(f"Name: {d.name}")
            print(f"Status: {d.status}")
            print(f"Ntrip Connected: {d.ntrip_connected}")
            print(f"Ntrip Status: {d.ntrip_status}")
            print(f"Service Config: {d.service_config}")
            print(f"System Info: {d.system_info}")

if __name__ == "__main__":
    asyncio.run(main())
