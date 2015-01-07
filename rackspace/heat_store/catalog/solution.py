import os
import hashlib
import re
import uuid
import yaml
from six.moves.urllib.parse import urlparse, urlunparse
import six.moves.urllib.request as urlrequest
from markdown import Markdown
from markdown.inlinepatterns import ImagePattern, ImageReferencePattern, \
    IMAGE_LINK_RE, IMAGE_REFERENCE_RE
try:
    from openstack_dashboard import api
except ImportError:
    api = None


class _RebasedImageLinkPattern(ImagePattern):
    """This class adds a base URL to any relative image links seen by the
    markdown parser."""
    def __init__(self, base_url, *args, **kwargs):
        self.base_url = base_url
        super(_RebasedImageLinkPattern, self).__init__(*args, **kwargs)

    def sanitize_url(self, url):
        url_parts = urlparse(url)
        if url_parts.scheme == '':
            url = os.path.join(self.base_url, url)
        return super(_RebasedImageLinkPattern, self).sanitize_url(url)


class _RebasedImageRefPattern(ImageReferencePattern):
    """This class adds a base URL to any relative image references seen by the
    markdown parser."""
    def __init__(self, base_url, *args, **kwargs):
        self.base_url = base_url
        super(_RebasedImageRefPattern, self).__init__(*args, **kwargs)

    def sanitize_url(self, url):
        url_parts = urlparse(url)
        if url_parts.scheme == '':
            url = os.path.join(self.base_url, url)
        return super(_RebasedImageRefPattern, self).sanitize_url(url)


class Solution(object):
    """This class holds a solution (heat template plus metadata for it).

    :param info_yaml: URL or filename to the solution's info.yaml file.
    :raises IOError: File not found.
            URLError: Remote file not found.
    """
    def __init__(self, info_yaml, basedir=''):
        """Import the solution's info.yaml file."""
        url_parts = urlparse(info_yaml)

        # read yaml content
        if url_parts.scheme == '':
            if not os.path.isabs(info_yaml):
                info_yaml = os.path.join(basedir, info_yaml)
                url_parts = urlparse(info_yaml)
            f = open(info_yaml, 'rt')
        else:
            f = urlrequest.urlopen(info_yaml)
        solution_yaml = f.read().decode('utf-8')

        self.base_url = urlunparse((url_parts.scheme, url_parts.netloc,
                                   os.path.dirname(url_parts.path),
                                   None, None, None))

        # create a markdown converter and modify it to rebase image links
        markdown = Markdown()
        markdown.inlinePatterns['image_link'] = _RebasedImageLinkPattern(
            self.base_url, IMAGE_LINK_RE, markdown)
        markdown.inlinePatterns['image_reference'] = _RebasedImageRefPattern(
            self.base_url, IMAGE_REFERENCE_RE, markdown)

        # import the solution's metadata
        info = yaml.load(solution_yaml)
        self.id = hashlib.md5(solution_yaml).hexdigest()
        self.title = info['title']
        self.logo = info['logo']
        self.short_description = info['short_description']
        self.template_version = info['template_version']
        self.highlights = info.get('highlights', [])
        self.links = info.get('links', [])
        self.heat_template = info['heat_template']
        self.env_file = info.get('env_file')  # environments are optional
        desc_url = info['long_description']
        url_parts = urlparse(desc_url)
        if url_parts.scheme == '' and not os.path.isabs(url_parts.path):
            desc_url = self._relative_to_absolute(info['long_description'])
            url_parts = urlparse(desc_url)
        if url_parts.scheme == '':
            f = open(desc_url)
        else:
            f = urlrequest.urlopen(desc_url)
        self.long_description = markdown.convert(f.read().decode('utf-8'))

    def _relative_to_absolute(self, rel):
        """Convert a filename relative to the solution's URL to absolute."""
        url_parts = urlparse(rel)
        if url_parts.scheme != '' or os.path.isabs(url_parts.path):
            return rel  # already absolute
        return os.path.join(self.base_url, rel)

    def _get_environment_data(self):
        """Return the contents of the heat environment file (if provided).

        This is necessary because the heat API does not accept a URL for
        this parameter.
        """
        if not self.env_file:
            return None
        f = urlrequest.urlopen(self.env_file)
        return f.read().decode('utf-8')

    def launch(self, request):
        """Launch the solution's heat template."""
        if not api or not api.heat:
            raise RuntimeError('Heat API is not available.')

        fields = {
            'stack_name': (re.sub('[\W\d]+', '_', self.title.strip()) +
                           '_' + str(uuid.uuid4())),
            'timeout_mins': 60,
            'disable_rollback': False,
            'parameters': {},
            # 'password': '',
            'template_url': self.heat_template,
            'environment': self._get_environment_data()  # can't use URL here
        }
        api.heat.stack_create(request, **fields)


class Catalog(object):
    """This class holds a collection of solutions, imported from yaml
    catalogs.

    :param args: one or more catalog filenames. Each filename must be a local
                 yaml file containing a list of solution URLs. Each solution
                 URL must point at the info.yaml file for the solution.
    """
    def __init__(self, *args):
        self.solutions = []
        for catalog in args:
            solutions = yaml.load(open(catalog).read())
            basedir = os.path.abspath(os.path.dirname(catalog))
            if solutions and len(solutions) > 0:
                for solution_url in solutions:
                    self.solutions.append(Solution(solution_url, basedir))

    def __iter__(self):
        return iter(self.solutions)

    def find_by_id(self, template_id):
        for template in self:
            if template.id == template_id:
                return template
