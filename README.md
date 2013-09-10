salt-beef
=========

A fabric file to set up a herd of servers in a panic.

Use this fabfile to play with rackspace, or bring up a load of servers,
quicksharp.


Usage
-----

1. Check out the repo somewhere.
2. `virtualenv salt-beef` (or `mkvirtualenv` etc.)
3. `pip install -r requirements.txt`
4. Manually export the env settings from postactivate.example (or edit it and
   place it in `$VIRTUAL_ENV/bin/postactivate`
5. Make a settings.py file from the example, filling in the settings as
   appropriate.
6. Run `fab connect:user=YOUR_USER make_saltmaster` (for example)
7. Run `fab -l` for more info!


Etymology
---------

"Treat your servers like cattle, not like pets." -- Spend your devops time
relaxing in a field, herding cattle, enjoying the rolling hills and the mildly
tempestuous weather.

There's no really good reason for naming all the commands after cow-related
activities, but then, since when was naming ever sane in technology?
