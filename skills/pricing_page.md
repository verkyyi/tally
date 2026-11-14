# Skill: pricing page
Read the listed price of the plan named in the question from the company's own pricing page.
- Use the monthly price when billed monthly if both monthly and annual prices are shown; say which one you used in the quote.
- Report the price in the currency the page shows. Do not convert.
- If the plan says "Contact sales", "Custom" or shows no number, return null. Never infer a price from another plan.
- Ignore add-ons, usage fees and per-credit prices unless the question asks for them.
