# Authorized contract change
Version 2 deliberately retires the legacy `allow_negative` option. The supported operation is now `charge(total, discount)` and discounts exceeding total are rejected. Compatibility with calls passing `allow_negative` is explicitly out of scope. The old test for that option was invalid under the new contract.
