import unittest

from cli import argv_valueof

class ArgvValueOfTest(unittest.TestCase):
    def test_arg_with_equals(self):
        argv = ['prog', '--opt=value']
        self.assertEqual(argv_valueof(argv, '--opt'), 'value')

    def test_arg_as_separate(self):
        argv = ['prog', '--opt', 'val']
        self.assertEqual(argv_valueof(argv, '--opt'), 'val')

    def test_missing_arg(self):
        argv = ['prog']
        self.assertIsNone(argv_valueof(argv, '--opt'))

if __name__ == '__main__':
    unittest.main()
