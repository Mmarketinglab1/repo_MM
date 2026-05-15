import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_PORT", "6543")

from database import SessionLocal
import models
import httpx
import asyncio

async def main():
    db = SessionLocal()
    companies = db.query(models.Company).all()
    print(f"Total companies: {len(companies)}")
    for company in companies:
        print(f"--- Company {company.name} ({company.id}) ---")
        print(f"whatsapp_token: {bool(company.whatsapp_token)}")
        print(f"whatsapp_phone_id: {bool(company.whatsapp_phone_id)}")
        print(f"whatsapp_waba_id: {company.whatsapp_waba_id}")
        
        if company.whatsapp_token and company.whatsapp_phone_id:
            token = company.whatsapp_token
            phone_id = company.whatsapp_phone_id
            waba_id = company.whatsapp_waba_id
            
            async with httpx.AsyncClient() as client:
                if not waba_id:
                    print("Attempting to discover WABA ID...")
                    url_discovery = f"https://graph.facebook.com/v20.0/{phone_id}?fields=whatsapp_business_account"
                    headers = {"Authorization": f"Bearer {token}"}
                    resp = await client.get(url_discovery, headers=headers)
                    print(f"Discovery response {resp.status_code}: {resp.text}")
                    data = resp.json()
                    if "whatsapp_business_account" in data:
                        waba_id = data["whatsapp_business_account"]["id"]
                        print(f"Discovered WABA ID: {waba_id}")
                
                if waba_id:
                    print(f"Fetching templates for WABA ID {waba_id}...")
                    url_templates = f"https://graph.facebook.com/v20.0/{waba_id}/message_templates"
                    headers = {"Authorization": f"Bearer {token}"}
                    resp_t = await client.get(url_templates, headers=headers)
                    print(f"Templates response {resp_t.status_code}:")
                    print(resp_t.text[:1000])

if __name__ == '__main__':
    asyncio.run(main())
