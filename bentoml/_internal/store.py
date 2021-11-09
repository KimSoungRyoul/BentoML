import datetime
import typing as t
from abc import ABC, abstractmethod
from contextlib import contextmanager

import fs
from fs.base import FS

from ..exceptions import BentoMLException, NotFound
from .types import PathType, Tag

T = t.TypeVar("T")


class StoreItem(ABC):
    @property
    @abstractmethod
    def tag(self) -> Tag:
        ...

    @classmethod
    @abstractmethod
    def from_fs(cls: t.Type[T], tag: Tag, fs: FS) -> T:
        pass

    @abstractmethod
    def creation_time(self) -> datetime.datetime:
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}({self.tag})"


Item = t.TypeVar("Item", bound=StoreItem)


class Store(ABC, t.Generic[Item]):
    """An FsStore manages items under the given base filesystem.

    Note that FsStore has no consistency checks; it assumes that no direct modification
    of the files in its directory has occurred.

    """

    fs: FS
    _item_type: t.Type[Item]

    @abstractmethod
    def __init__(self, base_path: PathType, item_type: t.Type[Item]):
        self._item_type = item_type
        self.fs = fs.open_fs(str(base_path))

    def list(self, tag: t.Optional[t.Union[Tag, str]] = None) -> t.List[Item]:
        if not tag:
            return [ver for _d in sorted(self.fs.listdir("/")) for ver in self.list(_d)]

        _tag = Tag.from_taglike(tag)
        if _tag.version is None:
            tags = sorted(
                [Tag(_tag.name, f.name) for f in self.fs.scandir(_tag.name) if f.is_dir]
            )
            return [self._get_item(t) for t in tags]
        else:
            return [self._get_item(_tag)] if self.fs.isdir(_tag.path()) else []

    def _get_item(self, tag: Tag) -> Item:
        """
        Creates a new instance of Item that represents the item with tag `tag`.
        """
        return self._item_type.from_fs(tag, self.fs.opendir(tag.path()))

    def get(self, tag: t.Union[Tag, str]) -> Item:
        """
        store.get("my_bento")
        store.get("my_bento:v1.0.0")
        store.get(Tag("my_bento", "latest"))
        """
        _tag = Tag.from_taglike(tag)
        if _tag.version is None or _tag.version == "latest":
            _tag.version = self.fs.readtext(_tag.latest_path())
        path = _tag.path()
        if self.fs.exists(path):
            return self._get_item(_tag)

        matches = self.fs.glob(f"{path}*")
        counts = matches.count().directories
        if counts == 0:
            raise NotFound(
                f"{self._item_type.__name__} '{tag}' is not found in BentoML store {self.fs}."
            )
        elif counts == 1:
            for match in matches:
                if match.is_dir:
                    return self._get_item(Tag(_tag.name, match.name))
        else:
            vers = []
            for match in matches:
                if match.is_dir:
                    vers += match.name
            raise BentoMLException(
                f"multiple versions matched by {_tag.version}: {vers}"
            )

    @contextmanager
    def register(self, tag: t.Union[str, Tag]):
        _tag = Tag.from_taglike(tag)

        item_path = _tag.path()
        if self.fs.exists(item_path):
            raise BentoMLException(
                f"Item '{_tag}' already exists in the store {self.fs}"
            )
        self.fs.makedirs(item_path)
        try:
            yield self.fs.getsyspath(item_path)
        finally:
            # item generation is most likely successful, link latest path
            with self.fs.open(_tag.latest_path(), "w") as latest_file:
                latest_file.write(_tag.version)

    def delete(self, tag: t.Union[str, Tag]) -> None:
        _tag = Tag.from_taglike(tag)

        self.fs.removetree(_tag.path())
        if self.fs.isdir(_tag.name):
            versions = self.list(_tag.name)
            if len(versions) == 0:
                # if we've removed all versions, remove the directory
                self.fs.removetree(_tag.name)
            else:
                new_latest = sorted(versions, key=self._item_type.creation_time)[0]
                # otherwise, update the latest version
                self.fs.writetext(_tag.latest_path(), new_latest.tag.name)