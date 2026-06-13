import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://postgres:aitogy%40aitogy@localhost:5432/auth_db')
    try:
        await conn.execute('ALTER TABLE rover_accounts ADD COLUMN is_shared_with_managers BOOLEAN DEFAULT FALSE')
        print("Column added successfully!")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
