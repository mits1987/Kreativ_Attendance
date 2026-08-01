"""Pure payroll arithmetic — mirrors the approved KREATIV GRAVURES salary sheet.

Zero Frappe imports so it can be unit-tested and reconciled offline.

APPROVED FORMULAS (from SALARY_SHEET_JUNE-2026_NEW.xlsx, verified against the
issued June 2026 salary slips and wage register):

    pay_days      = PD + WO + PH
    <component>_payable = ROUND(actual / days_in_month * pay_days, 0)
    gross         = sum(payable components) + incentive

    pf_wage       = basic_p + con_p + med_p + other_p        (excludes HRA, incentive)
    pf_wage_capped= min(pf_wage, 15000)
    pf            = ROUND(pf_wage_capped * 12%, 0)

    esi_wage      = basic_p                                   (basic only)
    esi           = CEIL(esi_wage * 0.75%)                     -- statutory: rounds UP

    pt            = 200 if gross >= 12000 else 0               (Gujarat slab)
    lwf           = 6

    total_ded     = pf + esi + pt + it + advance + loan + lwf
    net           = gross - total_ded

TWO DELIBERATE DEVIATIONS from literal spreadsheet cells, both to match the
salary slips that were actually issued:

  1. pf_wage: row 9 (KR052) uses `=T9` (basic only); every other row and BOTH
     issued slips use `T+V+W+X`. Treated as a typing error.
       KR052 sheet -> PF 1680, net 17942
       KR052 slip  -> PF 1800, net 17822   <-- we match this
  2. esi rounding: the sheet uses ROUND(); the slips and ESI statute round UP.
       KR016 sheet -> ESI 73, net 8520
       KR016 slip  -> ESI 74, net 8519     <-- we match this

OVERTIME (approved rule, confirmed by worked example 20000/243 -> 250 hrs -> 20576):
    required_hours = wd * standard_hours
    hourly_rate    = rate_of_wages / required_hours      (rate_of_wages = column R)
    pd             = min(wd, total_hours / standard_hours)
    ot_hours       = max(0, total_hours - required_hours)
    ot_amount      = ROUND(ot_hours * hourly_rate, 0)

Because pd is capped at wd, ot_hours > 0 implies pd == wd: overtime and partial
attendance are mutually exclusive.
"""

import math

PF_WAGE_CEILING = 15000.0
PF_RATE = 0.12
ESI_RATE = 0.0075
PT_THRESHOLD = 12000.0
PT_AMOUNT = 200.0
LWF_AMOUNT = 6.0


def _round(value):
    """Excel ROUND(): half away from zero. Python's round() is banker's rounding
    (round-half-to-even), which gives 0.5 -> 0 instead of 1. Must not be used here."""
    return float(math.floor(float(value) + 0.5)) if value >= 0 else -float(math.floor(-float(value) + 0.5))


def prorate(actual, days_in_month, pay_days):
    """ROUND(actual / days_in_month * pay_days, 0)."""
    if not days_in_month:
        return 0.0
    return _round(float(actual or 0) / float(days_in_month) * float(pay_days))


def compute_pay_days(pd, wo, ph):
    return float(pd or 0) + float(wo or 0) + float(ph or 0)


def compute_attendance(total_hours, standard_hours, wd):
    """Convert monthly worked hours to present days + overtime hours.

    Returns {"pd": float, "ot_hours": float, "required_hours": float}.
    pd is rounded to the nearest 0.5 (the granularity used on the register).
    """
    standard_hours = float(standard_hours or 0)
    wd = float(wd or 0)
    total_hours = float(total_hours or 0)
    if standard_hours <= 0:
        return {"pd": 0.0, "ot_hours": 0.0, "required_hours": 0.0}

    required_hours = wd * standard_hours
    raw_days = total_hours / standard_hours
    pd = min(wd, _round(raw_days * 2.0) / 2.0)
    ot_hours = max(0.0, total_hours - required_hours)
    return {"pd": pd, "ot_hours": round(ot_hours, 2), "required_hours": required_hours}


def compute_overtime_amount(ot_hours, rate_of_wages, wd, standard_hours):
    """ot_hours * (rate_of_wages / (wd * standard_hours)), rounded to rupees."""
    required_hours = float(wd or 0) * float(standard_hours or 0)
    if required_hours <= 0 or not ot_hours:
        return 0.0
    hourly_rate = float(rate_of_wages or 0) / required_hours
    return _round(float(ot_hours) * hourly_rate)


def compute_salary(
    basic=0, hra=0, con=0, med=0, other=0,
    pd=0, wo=0, ph=0, days_in_month=30,
    incentive=0, overtime=0,
    pf_applicable=False, esi_applicable=False,
    it=0, advance=0, loan=0, lwf=LWF_AMOUNT,
):
    """Full salary computation. Returns a dict of every intermediate figure."""
    pay_days = compute_pay_days(pd, wo, ph)

    basic_p = prorate(basic, days_in_month, pay_days)
    hra_p = prorate(hra, days_in_month, pay_days)
    con_p = prorate(con, days_in_month, pay_days)
    med_p = prorate(med, days_in_month, pay_days)
    other_p = prorate(other, days_in_month, pay_days)

    incentive = float(incentive or 0)
    overtime = float(overtime or 0)
    gross = basic_p + hra_p + con_p + med_p + other_p + incentive + overtime

    # --- PF: basic + con + med + other (no HRA, no incentive, no OT), capped ---
    pf = 0.0
    pf_wage = 0.0
    if pf_applicable:
        pf_wage = basic_p + con_p + med_p + other_p
        pf = _round(min(pf_wage, PF_WAGE_CEILING) * PF_RATE)

    # --- ESI: basic only, rounded UP ---
    esi = 0.0
    esi_wage = 0.0
    if esi_applicable:
        esi_wage = basic_p
        esi = float(math.ceil(esi_wage * ESI_RATE))

    pt = PT_AMOUNT if gross >= PT_THRESHOLD else 0.0
    lwf = float(lwf or 0)

    total_ded = pf + esi + pt + float(it or 0) + float(advance or 0) + float(loan or 0) + lwf

    return {
        "pay_days": pay_days,
        "basic_payable": basic_p, "hra_payable": hra_p, "con_payable": con_p,
        "med_payable": med_p, "other_payable": other_p,
        "incentive": incentive, "overtime": overtime,
        "gross": gross,
        "pf_wage": pf_wage, "pf": pf,
        "esi_wage": esi_wage, "esi": esi,
        "pt": pt, "lwf": lwf,
        "it": float(it or 0), "advance": float(advance or 0), "loan": float(loan or 0),
        "total_deduction": total_ded,
        "net": gross - total_ded,
    }
