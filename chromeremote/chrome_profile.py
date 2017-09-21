import os


profile_name = 'chrometemp.tar.gz'


def chrome_profile():
    return os.path.join(os.path.dirname(__file__), profile_name)
