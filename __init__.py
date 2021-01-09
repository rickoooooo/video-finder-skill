from mycroft import MycroftSkill, intent_file_handler, intent_handler
from mycroft.messagebus import Message
from mycroft.util.log import LOG
from plexapi.myplex import MyPlexAccount
import requests

class VideoFinder(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)
        # List of services. Names in config must match API names.
        self.services = self.settings.get('services').lower().split(',')

        # Some titles are only available in certain countries
        self.country = self.settings.get('country').lower()

        # RapidAPI keys for Utelly API and IMDB API
        self.header_apiKey = self.settings.get('x-rapidapi-key')

        # Plex info
        self.plexAccount = MyPlexAccount(self.settings.get('plexUsername'), self.settings.get('plexPassword'))
        self.plexServers = []

        # Service host info - from Rapid API
        self.header_utellyHost = self.settings.get('utellyHost')
        self.header_imdbHost = self.settings.get('imdbHost')

        # URLs for each service - from Rapid API
        self.utellyUrl = f'https://{self.header_utellyHost}/idlookup'
        self.imdbUrl = f'https://{self.header_imdbHost}/title/find'

        # If this is set to true, then the skill will offer to download a title if it can't be found.
        # Is there a way for this skill to just check if the other skill is loaded?
        self.couchPotato = self.settings.get('couchPotato')

    def initialize(self):
        # Find all Plex resources
        if "plex" in self.services:
            resources = self.plexAccount.resources()
            for resource in resources:
                if resource.provides == "server":
                    self.plexServers.append(resource.name)

    # --------
    # Main movie search handler 
    # --------
    @intent_handler('movie.search.intent')
    def handle_movie_search(self, message):
        # Pull data from intent
        search_title = message.data.get('title')
        search_actor = message.data.get('actor')

        # Talk to the user
        self.speak_dialog('search.for.that')

        # If the user specified an actor, search for the first matching title on IMDB to get the IMDB ID
        if search_actor:
            try:
                self.results = self.search_imdb_actor(search_title, search_actor)
                # Didn't find anything in IMDB
                if not self.results:
                    self.speak_dialog('what.movie')
                # Found one in IMDB, go find it via utelly, etc
                else:
                    self.movie_search(0)
            # Something went wrong with IMDB search
            except Exception as e:
                self.speak_dialog('unable.to.connect', data={'service':'imdb'})
                LOG.error("Error searching imdb: " + str(e))

        # If no actor was specified, find the top three and let the user choose the correct option
        else:
            # Make IMDB api request and get results
            try:
                # Search IMDB for IMDB ID (top three results)
                self.results = self.search_imdb(search_title)[0:3]
                self.speak_dialog('movies.results')
                # List top three videos found on IMDB for the user to chose from
                self.list_movies()
            # Something went wrong talking to IMDB
            except Exception as e:
                self.speak_dialog('unable.to.connect', data={'service':'imdb'})
                LOG.error("Error searching imdb: " + str(e))


    # List top three results found from IMDB
    def list_movies(self):
        # Loop through results 
        for result in self.results:
            title = result["title"]
            year = ""
            star = ""
            # Add year to response to help the user find the right title
            if "year" in result:
                year = ", " + str(result["year"])
            # Add first star of the title to help the user find the right title
            if "principals" in result:  
                star = "starring " + result["principals"][0]["name"]
            self.speak_dialog('movie.title', data={'title': title, 'year': year, 'star': star})
        # Let the user respond
        self.speak("", expect_response=True)

    # Performs search on IMDB. Returns first three results
    def search_imdb(self, title):
        # Build JSON query string
        queryString = {"q":title}
        LOG.info("Searching IMDB: " + str(queryString))

        # HTTP Request headers
        headers = {
            'x-rapidapi-key': self.header_apiKey,
            'x-rapidapi-host': self.header_imdbHost
        }

        # Make HTTP request
        response = requests.request("GET", self.imdbUrl, headers=headers, params=queryString)
        # Convert response to json
        data = response.json()

        return data["results"]

    # Performs search on IMDB with actor included. Returns first result found with the matching actor
    def search_imdb_actor(self, title, actor):
        # search IMDB and get all titles from the response
        results = self.search_imdb(title)
        LOG.info("actor: '" + actor + "'")

        # Loop through all titles and find the first one with the specified actor
        for result in results:
            if "principals" in result:
                for principal in result["principals"]:
                    if "name" in principal:
                        if principal["name"].lower() == actor.lower():
                            return result

        return False


    # -----------
    # User selected a movie from the list
    # -----------
    @intent_handler('select.movie.intent')
    def handle_select_movie_intent(self, message):
        movieNum = message.data.get("num")

        # Process options (first, second, third, invalid)
        if movieNum == "first" and len(self.results) >= 1:
            self.speak_dialog('search.for.that')
            self.movie_search(0)
        elif movieNum == "second" and len(self.results) >= 2:
            self.speak_dialog('search.for.that')
            self.movie_search(1)
        elif movieNum == "third" and len(self.results) >= 3:
            self.speak_dialog('search.for.that')
            self.movie_search(2)
        else:
            self.speak_dialog('not.valid.option')

    
    def movie_search(self, num):
        # Extract IMDB ID from result
        if "title" in self.results: # If there's only one result (search by actor) 
            imdbId = self.results["id"].split('/')[2]
        else:
            imdbId = self.results[num]["id"].split('/')[2]
        found_services = []

        # Search Utelly for IMDB ID
        try:
            locations = self.search_utelly(imdbId)
            if locations:
                for location in locations:
                    # We have a positive result
                    if location["display_name"].lower() in self.services:
                        found_services.append(location["display_name"])
        # Something went wrong taling to utelly
        except Exception as e:
            self.speak_dialog('unable.to.connect', data={'service':'utelly'})
            LOG.error("Error searching utelly: " + str(e))
        
        # If Plex is enabled (utelly doesn't support plex so we need a separate request)
        if "plex" in self.services:
            # Search plex
            try:
                plexService = self.search_plex(imdbId)
                # Positive result
                if plexService:
                    found_services.append('Plex - ' + plexService)
            # Something went wrong talking to Plex
            except Exception as e:
                self.speak_dialog('unable.to.connect', data={'service':'plex'})
                LOG.error("Error searching plex: " + str(e))

        # If at least one service was found
        if found_services:
            self.speak_dialog('found.services')
            # List all discovered services with matching result
            for service in found_services:
                self.speak_dialog('service', data={'service': service})
        # Didn't find the title anywhere
        else:
            self.not_found(num, imdbId)       


    # Performs search on Utelly and returns the raw response
    def search_utelly(self, imdbId):
        # Build HTP request query string
        queryString = {"source_id":imdbId,"source":"imdb","country":self.country}
        LOG.info("Searching Utelly: " + str(queryString))

        # Build HTTP request headers
        headers = {
            'x-rapidapi-key': self.header_apiKey,
            'x-rapidapi-host': self.header_utellyHost
        }

        # Make HTP request
        response = requests.request("GET", self.utellyUrl, headers=headers, params=queryString)

        # Parse response as json
        jsonData = response.json()

        # If there's a positive result, return it
        if "locations" in jsonData["collection"]:
            return jsonData["collection"]["locations"]
        # Didn't find the title in any service via utelly
        else:
            return None

    # Searches Plex
    def search_plex(self, imdbId):
        # Loop through available plex servers
        for server in self.plexServers:
            # Connect to a plex server
            plex = self.plexAccount.resource(server).connect()  # returns a PlexServer instance
            # Search libraries for IMDB ID
            videos = plex.library.search(guid = imdbId)
            for video in videos:
                # Positive match
                if imdbId in video.guid:
                    return server

        return False

    # Function is called if a title isn't found anywhere
    def not_found(self, num, imdbId):
        self.speak_dialog('could.not.find') 

        # If couchPotato skill is enabled
        if self.couchPotato:
            answer = ""
            # Ask the user if they want to download the title and keep asking until they say 'yes' or 'no'
            while answer != 'yes' and answer != 'no':
                answer = self.get_response('download.it')
            # User said yes, so download it
            if answer == 'yes':
                # Send an utterance to the BUS to trigger couchpotato download of the IMDB ID
                self.bus.emit(Message("recognizer_loop:utterance",  
                                {'utterances': ["Download the movie " + self.results[num - 1]["title"] + " with imdb id " + str(imdbId)],  
                                'lang': 'en-us'}))


def create_skill():
    return VideoFinder()

