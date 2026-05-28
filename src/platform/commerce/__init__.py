"""Hermes Commerce primitives — shared ordering substrate.

Four primitives, callable by existing portfolio agents (Catering, Flyer, future
order/upsell/loyalty agents). NOT a new dispatcher; NOT a new agent.

See:
- tasks/hermes-commerce-portfolio-reconciliation.md — ownership scope
- tasks/hermes-commerce-prd-v2.md — design

Slice 1 ships: cart + order_state + placeholder payment_link + audit chokepoint.
"""
