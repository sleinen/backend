"""
Cronjob tool that automatically pushes new ietf
draft yang modules to github repository old ones
are removed and naming is corrected to
<name>@<revision>.yang. New ietf RFC modules are
checked too but they are not automatically added.
E-mail is sent to yangcatalog admin users if such
thing occurs. Message about new RFC or DRAFT modules
is also sent to Cisco webex teams yangcatalog admin
room
"""
# Copyright 2018 Cisco and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

__author__ = "Miroslav Kovac"
__copyright__ = "Copyright 2018 Cisco and its affiliates"
__license__ = "Apache License, Version 2.0"
__email__ = "miroslav.kovac@pantheon.tech"

import argparse
import errno
import filecmp
import os
import shutil
import sys
import tarfile

import requests
from travispy import TravisPy
from travispy.errors import TravisError

import utility.log as log
from ietfYangDraftPull.draftPullLocal import check_name_no_revision_exist, \
    check_early_revisions
from utility import repoutil, messageFactory

if sys.version_info >= (3, 4):
    import configparser as ConfigParser
else:
    import ConfigParser


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-path', type=str,
                        default='/etc/yangcatalog/yangcatalog.conf',
                        help='Set path to config file')
    args = parser.parse_args()

    config_path = args.config_path
    config = ConfigParser.ConfigParser()
    config._interpolation = ConfigParser.ExtendedInterpolation()
    config.read(config_path)
    token = config.get('DraftPull-Section', 'yang-catalog-token')
    username = config.get('DraftPull-Section', 'username')
    commit_dir = config.get('DraftPull-Section', 'commit-dir')
    config_name = config.get('General-Section', 'repo-config-name')
    config_email = config.get('General-Section', 'repo-config-email')
    private_credentials = config.get('General-Section', 'private-secret').split(' ')
    log_directory = config.get('Directory-Section', 'logs')
    ietf_models_forked_url = config.get('General-Section', 'ietf-models-forked-repo-url')
    ietf_models_url_suffix = config.get('General-Section', 'ietf-models-repo-url_suffix')
    ietf_draft_url = config.get('General-Section', 'ietf-draft-private-url')
    ietf_rfc_url = config.get('General-Section', 'ietf-RFC-tar-private-url')
    LOGGER = log.get_logger('draftPull', log_directory + '/jobs/draft-pull.log')
    LOGGER.info('Starting Cron job IETF pull request')
    github_credentials = ''
    if len(username) > 0:
        github_credentials = username + ':' + token + '@'

    # Fork and clone the repository YangModles/yang
    LOGGER.info('Forking repository')
    reponse = requests.post(
        'https://' + github_credentials + ietf_models_url_suffix)
    repo = repoutil.RepoUtil(
        'https://' + token + '@github.com/' + username + '/yang.git')

    LOGGER.info('Cloning repo to local directory {}'.format(repo.localdir))
    repo.clone(config_name, config_email)
    try:
        LOGGER.info('Activating Travis')
        travis = TravisPy.github_auth(token)
    except:
        LOGGER.error(
            'Activating Travis - Failed. Removing local directory and deleting forked repository')
        requests.delete(ietf_models_forked_url,
                        headers={'Authorization': 'token ' + token})
        repo.remove()
        sys.exit(500)
    # Download all the latest yang modules out of https://new.yangcatalog.org/private/IETFDraft.json and store them in tmp folder
    LOGGER.info(
        'Loading all files from {}'.format(ietf_draft_url))
    ietf_draft_json = requests.get(ietf_draft_url
                                   , auth=(private_credentials[0], private_credentials[1])).json()
    try:
        os.makedirs(
            repo.localdir + '/experimental/ietf-extracted-YANG-modules/')
    except OSError as e:
        # be happy if someone already created the path
        if e.errno != errno.EEXIST:
            raise

    response = requests.get(ietf_rfc_url, auth=(private_credentials[0], private_credentials[1]))
    zfile = open(repo.localdir + '/tools/ietfYangDraftPull/rfc.tgz', 'wb')
    zfile.write(response.text)
    zfile.close()
    tgz = tarfile.open(repo.localdir + '/tools/ietfYangDraftPull/rfc.tgz')
    try:
        os.makedirs(
            repo.localdir + '/standard/ietf/RFCtemp')
    except OSError as e:
        # be happy if someone already created the path
        if e.errno != errno.EEXIST:
            raise
    tgz.extractall(repo.localdir + '/standard/ietf/RFCtemp')
    tgz.close()
    diff_files = []
    new_files = []

    for root, subdirs, sdos in os.walk(
                    repo.localdir + '/standard/ietf/RFCtemp'):
        for file_name in sdos:
            if '.yang' in file_name:
                if os.path.exists(repo.localdir + '/standard/ietf/RFC/'
                                          + file_name):
                    same = filecmp.cmp(repo.localdir + '/standard/ietf/RFC/'
                                       + file_name, root + '/' + file_name)
                    if not same:
                        diff_files.append(file_name)
                else:
                    new_files.append(file_name)
    shutil.rmtree(repo.localdir + '/standard/ietf/RFCtemp')
    os.remove(repo.localdir + '/tools/ietfYangDraftPull/rfc.tgz')
    if len(new_files) > 0 or len(diff_files) > 0:
        LOGGER.warning('new or modified RFC files found. Sending an E-mail')
        mf = messageFactory.MessageFactory()
        mf.send_new_rfc_message(new_files, diff_files)

    for key in ietf_draft_json:
        yang_file = open(
            repo.localdir + '/experimental/ietf-extracted-YANG-modules/' + key,
            'w+')
        yang_download_link = \
            ietf_draft_json[key][2].split('href="')[1].split('">Download')[0]
        try:
            yang_raw = requests.get(yang_download_link).text
            yang_file.write(yang_raw)
        except:
            LOGGER.warning('{} - {}'.format(key, yang_download_link))
            yang_file.write('')
        yang_file.close()
    check_name_no_revision_exist(
        repo.localdir + '/experimental/ietf-extracted-YANG-modules/')
    check_early_revisions(
        repo.localdir + '/experimental/ietf-extracted-YANG-modules/')
    try:
        travis_repo = travis.repo(username + '/yang')
        LOGGER.info('Enabling repo for Travis')
        travis_repo.enable()  # Switch is now on
        # Add commit and push to the forked repository
        LOGGER.info('Adding all untracked files locally')
        repo.add_all_untracked()
        LOGGER.info('Committing all files locally')
        repo.commit_all('Cronjob - every day pull of ietf draft yang files.')
        LOGGER.info('Pushing files to forked repository')
        LOGGER.info('Commit hash {}'.format(repo.repo.head.commit))
        with open(commit_dir, 'w+') as f:
            f.write('{}\n'.format(repo.repo.head.commit))
        repo.push()
    except TravisError as e:
        LOGGER.error('Error while pushing procedure {}'.format(e.message()))
        requests.delete(ietf_models_forked_url,
                        headers={'Authorization': 'token ' + token})
    except:
        LOGGER.error(
            'Error while pushing procedure {}'.format(sys.exc_info()[0]))
        requests.delete(ietf_models_forked_url,
                        headers={'Authorization': 'token ' + token})
    # Remove tmp folder
    LOGGER.info('Removing tmp directory')
    repo.remove()