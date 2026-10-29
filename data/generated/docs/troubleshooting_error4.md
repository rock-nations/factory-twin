---
doc_id: troubleshooting-error4
doc_type: troubleshooting
title: 'Troubleshooting error4: Coolant flow fault'
machine_model: null
component: null
error_type: error4
failure_id: null
synthetic: true
---

# Troubleshooting error4: Coolant flow fault

## Symptoms

The reported condition is **error4: Coolant flow fault**. Treat this as an indication to check the coolant delivery path and related equipment rather than as proof that a particular component has failed. Confirm when the fault occurs, whether it returns after reset, and whether coolant is visibly reaching the intended point. Record any operator observations and the machine state when error4 appears so that checks can be compared consistently.

## Likely causes

Follow-up records show that **Drive motor** failures were most frequent, with 128 instances within 7 days after error4. **Hydraulic pump** failures followed with 44 instances. **Coolant valve** failures were recorded 23 times, and **Spindle bearing** failures 19 times. These counts make Drive motor and Hydraulic pump the most prominent components associated with later failures in the supplied history; they do not establish that either component caused this error. Coolant valve and Spindle bearing also merit inspection as part of diagnosis. Use the counts to prioritize checks, not to replace direct testing or the machine’s approved service procedure.

## Sensor signatures

The supplied 24-hour sensor deviations are small overall; this code has no strong sensor signature. Reported directions and values are: volt, -0.0σ (downward); rotate, -0.0σ (downward); pressure, +0.4σ (upward); vibration, 0.0σ (no deviation). These measurements alone do not identify a failed component.

## Troubleshooting steps

1. Record the error timing, operating conditions, and any visible coolant delivery issue. Follow site safety rules and the machine’s lockout procedure before opening guards or servicing equipment.
2. Check the coolant reservoir level and inspect accessible hoses and connections for blockage, kinks, leaks, or disconnection. Correct only issues permitted by the service procedure.
3. Inspect the **Coolant valve** and its connections for visible damage or obstruction. Verify operation using the approved diagnostic procedure; do not bypass the valve.
4. Check the **Hydraulic pump** and its supply path for visible leaks, loose connections, or abnormal operation. Escalate if testing requires instruments or procedures beyond your authorization.
5. Inspect the **Drive motor** and **Spindle bearing** for visible damage or abnormal noise, following the machine’s prescribed inspection process. Do not run equipment with unsafe signs.
6. After any approved correction, restore guards, clear the area, and test the machine using the specified procedure. Record whether error4 returns and share findings with maintenance if it persists.
