import unittest
import numpy as np

# Copied logic for testing before integration
def mtf(m, x):
    """
    Midtones Transfer Function.
    (m - 1) * x / ((2 * m - 1) * x - m)
    """
    if m == 0.5:
        return x
    return (m - 1) * x / ((2 * m - 1) * x - m)

def get_mtf_balance(median, target_bg=0.25):
    """
    Find midtones balance parameter 'm' 
    such that MTF(m, median) = target_bg.
    
    Formula: m = x(1-y) / (x + y - 2xy)
    """
    x = float(median)
    y = float(target_bg)
    
    # Boundary checks
    if x <= 0: return 0.0 # Extreme low
    if x >= 1: return 1.0 # Extreme high
    if x == y: return 0.5
    
    denom = x + y - 2 * x * y
    if denom == 0:
        return 0.5
        
    m = (x * (1 - y)) / denom
    return m

class TestSTF(unittest.TestCase):
    def test_mtf_balance(self):
        # 1. Identity
        m = get_mtf_balance(0.25, 0.25)
        self.assertAlmostEqual(m, 0.5)
        
        # 2. Stretch Dark
        # Median 0.1 -> Target 0.25
        m = get_mtf_balance(0.1, 0.25)
        print(f"Median=0.1 -> m={m}")
        # Check result
        res = mtf(m, 0.1)
        self.assertAlmostEqual(res, 0.25)
        
        # 3. Compress Bright
        # Median 0.8 -> Target 0.25
        m = get_mtf_balance(0.8, 0.25)
        print(f"Median=0.8 -> m={m}")
        res = mtf(m, 0.8)
        self.assertAlmostEqual(res, 0.25)

    def test_mtf_identity(self):
        vals = np.array([0.0, 0.5, 1.0])
        res = mtf(0.5, vals)
        np.testing.assert_array_almost_equal(res, vals)

if __name__ == '__main__':
    unittest.main()
