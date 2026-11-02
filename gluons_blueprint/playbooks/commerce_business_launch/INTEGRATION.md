# Implementation assembly for Commerce Business Launch

Build as a meta-playbook using Gluons' existing durable runner and business connectors, not as a monolithic agent. Existing repository components may supply connectors, document extraction, email handling and storefront assets, subject to their license restrictions. The Shopify merchant signup/transfer and payment provider verification flows must use approved provider processes. Do not bypass KYC or merchant agreements.

Adapters required: Shopify store-owner connection and verified permissions; payment provider status-only adapter; supplier email and verified source collector; shipping/fulfillment provider; audit, approvals and cost service. Check `docs/14_business_launch_owner_portal_and_identity.md` for legal/technical boundaries and acceptance tests. For YC, use dev/client-transfer sandbox with honest restrictions and real API responses where possible.
