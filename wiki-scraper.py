#!/usr/bin/env python3

from concurrent.futures import ThreadPoolExecutor
from mwclient import Site
import json
import os
import platform
import re
import requests
import remotezip
import shutil
import subprocess
import sys
import tempfile
import time
import wikitextparser as wtp


class BetaScraper:
    def __init__(self, site: Site):
        self.site = site
        self.api = dict()
        self.device_regex = re.compile(r'(iPhone|AppleTV|iPad|iPod)[0-9]+,[0-9]+')

    def build_api(self, device_types: tuple) -> None:
        for result in self.site.search('Beta Firmware/'):
            if ('.x' not in result['title']) or (not any(x in result['title'] for x in device_types)):
                continue

            major_version = int(result['title'].split('/')[2][:-2])
            if major_version < 9 if 'Apple TV' not in result['title'] else 7: # All beta firmwares pre-iOS 9/tvOS 7 aren't IPSW beta firmwares
                continue

            wiki_page = wtp.parse(self.site.pages[result['title']].text())
            for table in wiki_page.tables:
                template = table.data()[0]
                for firm in range(1, len(table.data())):
                    firm_data = [x for x in table.data()[firm] if x is not None]
                    devices = list()

                    for device in wtp.parse(firm_data[next(template.index(x) for x in template if any(i in x for i in ('Codename', 'Keys')))]).wikilinks:
                        regex = self.device_regex.match(str(device.text))
                        if regex is not None:
                            devices.append(regex.group())

                    firm = dict()

                    version = wtp.parse(firm_data[0])
                    if version.wikilinks:
                        firm['version'] = firm_data[0].replace(str(version.wikilinks[0]), version.wikilinks[0].text)
                    else:
                        firm['version'] = firm_data[0]

                    buildids = firm_data[1].split('   | ') # Can't use the mediawiki parser for this, unfortunately
                    if len(buildids) > 1:
                        buildids.pop(0)
                        buildids.pop(-1)
                        for b in range(len(buildids)):
                            buildids[b] = buildids[b].split(' = ')[-1][:-1]

                    try:
                        ipsws = next(wtp.parse(item).external_links for item in firm_data if wtp.parse(item).external_links)
                    except: # No URLs for this firmware, skip
                        continue

                    if not any(ipsw.url.endswith('.ipsw') for ipsw in ipsws): # Only IPSW beta firmwares are scraped
                        continue

                    ipsw_sizes = list()
                    for word in [x.replace(',', '').replace('\n', '') for x in firm_data[-1].split(' ')]:
                        if (word.isnumeric()) and (int(word) > 10):
                            ipsw_sizes.append(int(word))

                    if len(ipsw_sizes) != len(ipsws): # One or more IPSWs don't have filesizes, skip
                        continue

                    for d in range(len(devices)):
                        firm_index = 0 if ((len(devices) == 4) and (d in (0, 1))) or ((len(devices) == 2 ) and (d == 0)) else 1
                        firm['buildid'] = buildids[0]
                        if len(buildids) > 1:
                            firm['buildid'] = buildids[firm_index]

                        firm['url'] = ipsws[0].url
                        firm['size'] = ipsw_sizes[0]

                        if len(ipsws) > 1:
                            firm['url'] = ipsws[firm_index].url
                            firm['size'] = ipsw_sizes[firm_index]

                        if len(firm.keys()) < 4: # Incomplete firmware info, skipping
                            continue

                        if devices[d] not in self.api.keys():
                            self.api[devices[d]] = list()

                        if not any(f['buildid'] == firm['buildid'] for f in self.api[devices[d]]):
                            self.api[devices[d]].append(firm)

    def get_signing_status(self, device: str) -> None: #TODO: Remove tsschecker dependency + make this a LOT quicker
        boardconfig = requests.get(f'https://api.ipsw.me/v4/device/{device}').json()['boards'][0]['boardconfig']
        with tempfile.TemporaryDirectory() as tmpdir:
            for firm in self.api[device]:
                try:
                    with remotezip.RemoteZip(firm['url']) as ipsw:
                        manifest = next(f for f in ipsw.namelist() if 'Manifest' in f)
                        ipsw.extract(manifest, tmpdir)
                except:
                    self.api[device].pop(self.api[device].index(firm))
                    continue

                args = (
                    'tsschecker',
                    '-d',
                    device,
                    '-B',
                    boardconfig,
                    '-m',
                    f'{tmpdir}/{manifest}'
                )

                tsschecker = subprocess.run(args, stdout=subprocess.PIPE, universal_newlines=True)
                firm['signed'] = True if 'IS being signed!' in tsschecker.stdout else False

    def write_api(self, path: str) -> None:
        if os.path.exists(path):
            shutil.rmtree(path)

        os.mkdir(path)
        for device in self.api.keys():
            with open(f'{path}/{device}', 'w') as f:
                json.dump(sorted(self.api[device], key=lambda firm: firm['buildid'], reverse=True), f)

def main() -> None:
    if platform.system() == 'Windows':
        sys.exit('[ERROR] Windows is not supported. Exiting.')

    if shutil.which('tsschecker') is None:
        sys.exit('[ERROR] tsschecker is not installed. Exiting.')

    start_time = time.time()
    scraper = BetaScraper(Site('www.theiphonewiki.com'))

    print('[1] Scraping The iPhone Wiki...')
    with ThreadPoolExecutor() as executor:
        executor.submit(scraper.build_api, ('Apple TV',))
        executor.submit(scraper.build_api, ('iPod touch',))
        executor.submit(scraper.build_api, ('iPhone',))
        executor.submit(scraper.build_api, ('iPad', 'iPad Air', 'iPad Pro', 'iPad Mini'))

    print('[2] Grabbing signing status (this will take a while, please wait)...')
    with ThreadPoolExecutor() as executor:
        for device in scraper.api.keys():
            executor.submit(scraper.get_signing_status, device)

    print('[3] Writing out API...')
    scraper.write_api('betas')

    print(f'Done! Took {round(time.time() - start_time)}s.')

if __name__ == "__main__":
    main()
