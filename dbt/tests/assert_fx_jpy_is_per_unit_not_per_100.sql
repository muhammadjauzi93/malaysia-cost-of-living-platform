-- Singular test: the classic unit slip. If JPY is divided by 100 once and then again elsewhere,
-- or never divided at all, its ringgit value jumps by a factor of 100. One yen is about RM0.03,
-- so anything at or above RM0.10 per unit means the quote unit was not applied.
select currency_code, month_date, rate_myr_per_unit
from {{ ref('int_fx_monthly_myr') }}
where currency_code = 'JPY'
  and rate_myr_per_unit >= 0.10
