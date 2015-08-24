###
# Copyright (c) 2015, Moritz Lipp
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import json

from supybot.commands import *
import supybot.ircdb as ircdb
import supybot.ircmsgs as ircmsgs
import supybot.callbacks as callbacks
import supybot.log as log
import supybot.httpserver as httpserver
try:
    from supybot.i18n import PluginInternationalization
    from supybot.i18n import internationalizeDocstring
    _ = PluginInternationalization('Gitlab')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    def _(x):
        return x

    def internationalizeDocstring(x):
        return x


class GitlabHandler(object):
    """Handle gitlab messages"""

    def __init__(self, plugin, irc):
        self.irc = irc
        self.plugin = plugin
        self.log = log.getPluginLogger('Gitlab')

    def handle_payload(self, headers, payload):
        if 'X-Gitlab-Event' not in headers:
            self.log.info('Invalid header: Missing X-Gitlab-Event entry')
            return

        event_type = headers['X-Gitlab-Event']
        if event_type not in ['Push Hook', 'Tag Push Hook', 'Note Hook', 'Issue Hook', 'Merge Request Hook']:
            self.log.info('Unsupported X-Gitlab-Event type')
            return

        # Check if any channel has subscribed to this project
        for channel in self.irc.state.channels.keys():
            projects = self.plugin._load_projects(channel)
            for slug, url in projects.items():
                # Parse project url
                if event_type == 'Push Hook' or event_type == 'Tag Push Hook' or event_type == 'Note Hook':
                    if url != payload['repository']['homepage']:
                        continue
                elif event_type == 'Issue Hook' or event_type == 'Merge Request Hook':
                    if url not in payload['object_attributes']['url']:
                        continue
                else:
                    continue

                # Handle types
                if event_type == 'Push Hook':
                    self._push_hook(channel, slug, payload)
                elif event_type == 'Tag Push Hook':
                    self._tag_push_hook(channel, slug, payload)
                elif event_type == 'Issue Hook':
                    self._issue_hook(channel, slug, payload)
                elif event_type == 'Note Hook':
                    self._note_hook(channel, slug, payload)
                elif event_type == 'Merge Request Hook':
                    self._merge_request_hook(channel, slug, payload)

    def _push_hook(self, channel, slug, payload):
        payload['project'] = {
            'id': payload['project_id'],
            'name': slug
        }

        # Send general message
        msg = self._build_message(channel, 'push', payload)
        self._send_message(channel, msg)

        # Send commits
        for commit in payload['commits']:
            commit['project'] = {
                'id': payload['project_id'],
                'name': slug
            }
            commit['short_id'] = commit['id'][0:10]

            msg = self._build_message(channel, 'commit', commit)
            self._send_message(channel, msg)

    def _tag_push_hook(self, channel, slug, payload):
        pass

    def _note_hook(self, channel, slug, payload):
        pass

    def _issue_hook(self, channel, slug, payload):
        pass

    def _merge_request_hook(self, channel, slug, payload):
        pass

    def _build_message(self, channel, format_string_identifier, args):
        format_string = str(self.plugin.registryValue('format.' + format_string_identifier, channel))
        msg = format_string.format(**args)
        return msg

    def _send_message(self, channel, msg):
        priv_msg = ircmsgs.privmsg(channel, msg)
        self.irc.queueMsg(priv_msg)


class GitlabWebHookService(httpserver.SupyHTTPServerCallback):
    """https://gitlab.com/gitlab-org/gitlab-ce/blob/master/doc/web_hooks/web_hooks.md"""

    name = "GitlabWebHookService"
    defaultResponse = """This plugin handles only POST request, please don't use other requests."""

    def __init__(self, plugin, irc):
        self.log = log.getPluginLogger('Gitlab')
        self.gitlab = GitlabHandler(plugin, irc)
        self.plugin = plugin
        self.irc = irc

    def _send_error(self, handler, message):
        handler.send_response(403)
        handler.send_header('Content-type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(message.encode('utf-8'))

    def _send_ok(self, handler):
        handler.send_response(200)
        handler.send_header('Content-type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(bytes('OK', 'utf-8'))

    def doPost(self, handler, path, form):
        headers = dict(self.headers)

        network = None
        channel = None

        try:
            information = path.split('/')[1:]
            network = information[0]
            channel = '#' + information[1]
        except IndexError:
            self._send_error(handler, _("""Error: You need to provide the
                                        network name and the channel in
                                        url."""))
            return

        if self.irc.network != network or channel not in self.irc.state.channels:
            return

        # Handle payload
        try:
            payload = json.JSONDecoder().decode(form.decode('utf-8'))
        except Exception as e:
            self._send_error(handler, _('Error: Invalid JSON data sent.'))

        try:
            self.gitlab.handle_payload(headers, payload)
        except Exception as e:
            self.log.info(e)
            self._send_error(handler, _('Error: Invalid data sent.'))

        # Return OK
        self._send_ok(handler)


class Gitlab(callbacks.Plugin):
    """Plugin for communication and notifications of a Gitlab project management tool instance"""
    threaded = True

    def __init__(self, irc):
        global instance
        super(Gitlab, self).__init__(irc)
        instance = self

        callback = GitlabWebHookService(self, irc)
        httpserver.hook('gitlab', callback)

    def die(self):
        httpserver.unhook('gitlab')

        super(Gitlab, self).die()

    def _load_projects(self, channel):
        projects_string = self.registryValue('projects', channel)
        if projects_string is None or len(projects_string) == 0:
            return {}
        else:
            return json.loads(projects_string)

    def _save_projects(self, projects, channel):
        string = ''
        if projects is not None:
            string = json.dumps(projects)
        self.setRegistryValue('projects', value=string, channel=channel)

    def _check_capability(self, irc, msg):
        if ircdb.checkCapability(msg.prefix, 'admin'):
            return True
        else:
            irc.errorNoCapability('admin')
            return False

    class gitlab(callbacks.Commands):
        """Gitlab commands"""

        class project(callbacks.Commands):
            """Project commands"""

            @internationalizeDocstring
            def add(self, irc, msg, args, channel, project_slug, project_url):
                """[<channel>] <project-slug> <project-url>

                Announces the changes of the project with the slug <project-slug>
                and the url <project-url> to <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                projects = instance._load_projects(channel)
                if project_slug in projects:
                    irc.error(_('This project is already announced to this channel.'))
                    return

                # Save new project mapping
                projects[project_slug] = project_url
                instance._save_projects(projects, channel)

                irc.replySuccess()

            add = wrap(add, ['channel', 'somethingWithoutSpaces', 'httpUrl'])

            @internationalizeDocstring
            def remove(self, irc, msg, args, channel, project_slug):
                """[<channel>] <project-id>

                Stops announcing the changes of the project slug <project-slug> to
                <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                projects = instance._load_projects(channel)
                if project_slug not in projects:
                    irc.error(_('This project is not registered to this channel.'))
                    return

                # Remove project mapping
                del projects[project_slug]
                instance._save_projects(projects, channel)

                irc.replySuccess()

            remove = wrap(remove, ['channel', 'somethingWithoutSpaces'])

            @internationalizeDocstring
            def list(self, irc, msg, args, channel):
                """[<channel>]

                Lists the registered projects in <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                projects = instance._load_projects(channel)
                if projects is None or len(projects) == 0:
                    irc.error(_('This channel has no registered projects.'))
                    return

                for project_slug, project_url in projects.items():
                    irc.reply("%s: %s" % (project_slug, project_url))

            list = wrap(list, ['channel'])


Class = Gitlab


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
