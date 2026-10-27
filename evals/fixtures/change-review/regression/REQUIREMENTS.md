# Active contract
`charge(total, discount)` must reject a discount greater than total. Negative charges must never be emitted. This requirement remains active across both revisions. A regression test for the rejection was intentionally present in the base.
