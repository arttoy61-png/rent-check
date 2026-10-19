# rent-check
화곡동 월세 시세 검증 도구

## Public Gangseo transaction data

`gangseo_transactions_public.json` is the shared public MOLIT transaction snapshot used by the house-price and nearby monthly-rent/jeonse comparison tools. It contains public transaction fields and building coordinates, with sale, jeonse and monthly-rent categories for apartments, villas and officetels. Amounts are in 만원; coordinates are `[latitude, longitude]`.

Check `schema_version`, `generated_at` and `updated` before interpreting freshness. Recent months may still receive reports and corrections. Missing coordinates exclude a transaction from distance-based comparisons, not ordinary price/search results. This repository publishes the generated data; generation code is maintained separately.
