import spotipy
spotify = spotipy.Spotify()
import sys
import pandas as pd
from spotipy.oauth2 import SpotifyClientCredentials
from bs4 import BeautifulSoup
import requests
import lxml
from multiprocessing import Pool

def get_spotify_credentials(filename):
    if filename is None:
        raise IOError('Credentials file is none.')

    f = open(filename)

    txt = f.readlines()
    client_id = None
    client_secret = None
    for l in txt:
        l = l.replace('\n', '').split(' ')
        if l[0] == 'client_id':
            client_id = l[1]
        elif l[0] == 'client_secret':
            client_secret = l[1]

    client_credentials_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
    sp.trace = False

    return sp

def get_spotify_data(artist_name, credentials_file):

    # get authorisation stuff
    sp = get_spotify_credentials(credentials_file)

    # first get spotify artist uri
    results = sp.search(q='artist:' + artist_name, type='artist')
    items = results['artists']['items']
    if len(items) > 0:
        artist = items[0]

    uri = artist['uri']

    # now get album uris
    results = sp.artist_albums(uri, album_type='album')
    albums = results['items']
    while results['next']:
        results = sp.next(results)
        albums.extend(results['items'])

    uris = []
    track_names = []
    album_names = []

    # get track data
    for album in albums:
        for t in sp.album(album['uri'])['tracks']['items']:
            uris.append(t['uri'])
            track_names.append(t['name'])
            album_names.append(album['name'])
    features = []
    for i in range(len(uris)// 100 + 1):
        fs = sp.audio_features(uris[i*100:min((i+1)*100, len(uris))])
        if fs[0] is not None:
            features.extend(fs)

    # make dataframe
    dat = pd.DataFrame(features)
    dat['track_name'] = track_names
    dat['album'] = album_names
    dat['artists'] = artist_name

    # ignore live, remix and deluxe album versions
    mask = [('live' not in s.lower() and 'deluxe' not in s.lower()
             and 'remix' not in s.lower() and 'rmx' not in s.lower()
            and 'remastered' not in s.lower()) for s in dat.album.values]
    dat = dat[mask]
    mask2 = [(('remix' not in s.lower()) and
              'remastered' not in s.lower() and 'live' not in s.lower()
             and 'version' not in s.lower()) for s in dat.track_name.values]
    dat = dat[mask2]

    dat.set_index('track_name', inplace=True)
    dat.drop_duplicates(inplace=True)
    dat = dat[~dat.index.duplicated(keep='first')]

    return dat

def get_featured_playlist_data(credentials_file=None,
                              locale = None, country = None, timestamp = None, limit = 50, offset = 0):
    sp = get_spotify_credentials(credentials_file)
    featured_play = sp.featured_playlists(locale = locale,country = country,
                                          timestamp = timestamp,
                                          limit = limit, offset= offset)
    dat = pd.io.json.json_normalize(featured_play["playlists"]["items"])
    dat["region"] = "US"
    dat["date"] = timestamp
    dat = dat.drop(["images","owner.display_name",
        "owner.external_urls.spotify","owner.href","owner.id",
        "owner.uri"],axis = 1)
    return dat
    
def get_spotify_playlist_data(username='spotify', playlist=None, credentials_file=None):

    # set a limit to total number of tracks to analyse
    track_number_limit = 500

    # get authorisation stuff
    sp = get_spotify_credentials(credentials_file)

    # get user playlists
    p = None
    results = sp.user_playlists(username)
    playlists = results['items']

    if playlist is None: # use first of the user's playlists
        playlist = playlists[0]['name']

    for pl in playlists:
        if pl['name'] is not None and pl['name'].lower() == playlist.lower():
            p = pl
            break
    while results['next'] and p is None:
        results = sp.next(results)
        playlists = results['items']
        for pl in playlists:
            if pl['name'] is not None and pl['name'].lower() == playlist.lower():
                p = pl
                break

    if p is None:
        print('Could not find playlist')
        return
    results = sp.user_playlist(p['owner']['id'], p['id'], fields="tracks,next")['tracks']
    tracks = results['items']
    while results['next'] and len(tracks) < track_number_limit:
        results = sp.next(results)
        if results['items'][0] is not None:
            tracks.extend(results['items'])

    ts = []
    track_names = []

    for t in tracks:
        track = t['track']
        track['album'] = track['album']['name']
        track_names.append(t['track']['name'])
        artists = []
        for a in track['artists']:
            artists.append(a['name'])
        track['artists'] = ', '.join(artists)
        ts.append(track)

    uris = []
    dat = pd.DataFrame(ts)

    dat.drop(['available_markets', 'disc_number', 'external_ids', 'external_urls'], axis=1, inplace=True)

    features = []
    # loop to take advantage of spotify being able to get data for 100 tracks at once
    for i in range(len(dat)// 100 + 1):
        fs = sp.audio_features(dat.uri.iloc[i*100:min((i+1)*100, len(dat))])
        if fs[0] is not None:
            features.extend(fs)

    fs = pd.DataFrame(features)
    dat = pd.concat([dat, fs], axis=1)
    dat['track_name'] = track_names
    """
    # ignore live, remix and deluxe album versions
    mask = [(('live' not in s.lower()) and ('deluxe' not in s.lower())
             and ('remix' not in s.lower())) for s in dat.album.values]
    dat = dat[mask]
    mask2 = [(('remix' not in s.lower()) and
              'remastered' not in s.lower()
             and 'version' not in s.lower()) for s in dat.track_name.values]
    dat = dat[mask2]
    """
    dat.set_index('track_name', inplace=True)
    dat = dat[~dat.index.duplicated(keep='first')]
    dat = dat.T[~dat.T.index.duplicated(keep='first')].T

    return dat

def search_genius(query, credentials_file, return_artist_id=True):
    f = open(credentials_file)
    txt = f.readlines()
    genius_token = None
    for l in txt:
        l = l.replace('\n', '').split(' ')
        if l[0] == 'genius_token':
            genius_token = l[1]

    API = 'https://api.genius.com'
    HEADERS = {'Authorization': 'Bearer ' + genius_token}

    search_endpoint = API + '/search?'
    payload = {'q': query}
    search_request_object = requests.get(search_endpoint, params=payload, headers=HEADERS)

    if search_request_object.status_code == 200:
        s_json_response = search_request_object.json()
        api_call = search_request_object.url

        if len(s_json_response['response']['hits']) == 0:
            return None
        else:
            hit = s_json_response['response']['hits'][0]

            artist = hit['result']['primary_artist']['name']
            artist_url = hit['result']['primary_artist']['url']
            artist_id = hit['result']['primary_artist']['id']
            title = hit['result']['title']
            song_id = hit['result']['id']
            url = hit['result']['url']

            if return_artist_id:
                return artist_id #, artist_url, title, url, hit
            else:
                return url

    elif 400 <= search_request_object.status_code < 500:
        print('[!] Uh-oh, something seems wrong...')
        print('[!] Please submit an issue at https://github.com/donniebishop/genius_lyrics/issues')
        sys.exit(1)

    elif search_request_object.status_code >= 500:
        print('[*] Hmm... Genius.com seems to be having some issues right now.')
        print('[*] Please try your search again in a little bit!')
        sys.exit(1)

    return

def get_songs(artist_id, credentials_file):
    f = open(credentials_file)
    txt = f.readlines()
    genius_token = None
    for l in txt:
        l = l.replace('\n', '').split(' ')
        if l[0] == 'genius_token':
            genius_token = l[1]

    API = 'https://api.genius.com'
    HEADERS = {'Authorization': 'Bearer ' + genius_token}
    songs = {}
    page = 1
    search_endpoint = API + '/artists/' + str(artist_id) + '/songs'

    while True:
        payload = {'per_page': 50, 'page': page}
        search_request_object = requests.get(search_endpoint, params=payload, headers=HEADERS)

        if search_request_object.status_code != 200:
            break
        else:
            s_json_response = search_request_object.json()
            if len(s_json_response['response']['songs']) == 0:
                break
            for song in s_json_response['response']['songs']:
                songs[song['title']] = (song['id'], song['url'])
            page += 1
    return songs

def get_lyrics(url):
    if type(url) is dict:
        urls = [i[1] for i in list(url.values())]
        p = Pool(20)
        records = p.map(requests.get, urls)
        p.terminate()
        p.join()
        lyrics = []
        for record in records:                
            song_soup = BeautifulSoup(record.text, 'lxml')
            divs = song_soup.find_all('div')

            lyric = []
            for d in divs:
                try:
                    if d['class'][0] == 'lyrics':
                        strings = d.stripped_strings
                except KeyError:
                    pass

            for s in strings:
                #if s[0] != '[':
                lyric.append(s)
            ls = '\n'.join(lyric)
            lyrics.append(ls)
        return lyrics
    else:
        get_url = requests.get(url)
        song_soup = BeautifulSoup(get_url.text, 'lxml')
        divs = song_soup.find_all('div')

        lyrics = []
        for d in divs:
            try:
                if d['class'][0] == 'lyrics':
                    strings = d.stripped_strings
            except KeyError:
                pass

        for s in strings:
            #if s[0] != '[':
            lyrics.append(s)
        ls = '\n'.join(lyrics)

        return ls

def get_playlist_urls(df, credentials_file):
    # get the urls for the lyrics of all the songs in a dataframe
    if 'genius_url' in df.columns and df.iloc[0].genius_url is not None:
        return
    df['genius_url'] = None

    for i, r in df.iterrows():
        try:
            url = search_genius(r['artists'] + ', ' + i, credentials_file, return_artist_id=False)
            df.set_value(i, 'genius_url', url)
        except IndexError:
            pass

def get_playlist_lyrics(df, credentials_file):
    # get the lyrics for all the songs in a dataframe
    get_playlist_urls(df, credentials_file)

    if 'lyrics' in df.columns and df.iloc[0].lyrics is not None:
        return
    df['lyrics'] = None

    for i, r in df.iterrows():
        if r['genius_url'] is not None:
            lyrics = get_lyrics(r['genius_url'])
            df.set_value(i, 'lyrics', lyrics)
