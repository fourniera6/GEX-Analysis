import numpy as np

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)


def bs_gamma(spot, strike, t, iv, r: float, q: float):
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r - q + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    return np.exp(-q * t - 0.5 * d1 * d1) * _INV_SQRT_2PI / (spot * iv * sqrt_t)
