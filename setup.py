from setuptools import setup

setup(
        name='chrome_remote_shell',
        version='0.1.4',
        description='Client for remote debugging Google Chrome',
        url='https://github.com/tempelkim/chrome-remote-shell',
        author='Boris Kimmina',
        author_email='kim@kimmina.net',
        license='MIT',
        packages=['chromeremote'],
        package_data={'': ['*.gz']},
        install_requires=[
            'websocket-client',
        ],
        zip_safe=False,
)
