from .base import BaseWorker
from syft.codes import MSGTYPE
import syft as sy
import random


class PlanPointer(BaseWorker):
    def __init__(self, location, id_at_location, register, owner, *args, **kwargs):
        super().__init__(hook=sy.hook, *args, **kwargs)

        self.location = location
        self.id_at_location = id_at_location
        self.owner = owner

    def __call__(self, response_ids, *args):

        args = [args, response_ids]
        command = ("execute_plan", self, args)

        print('send exe cmd to ', self.location.id)
        response = sy.local_worker.send_command(
            message=command, recipient=self.location, return_ids=response_ids
        )
        return response

    def _recv_msg(self, message):
        ""

    def _send_msg(self, message, location):
        ""


def replace_ids(obj, change_id, to_id, from_worker, to_worker):
    _obj = list()

    for i, item in enumerate(obj):
        if isinstance(item, int) and (item == change_id):
            _obj.append(to_id)

        elif isinstance(item, type(from_worker)) and (item == from_worker):
            _obj.append(to_worker)

        elif isinstance(item, (list, tuple)):
            _obj.append(
                replace_ids(
                    obj=item,
                    change_id=change_id,
                    to_id=to_id,
                    from_worker=from_worker,
                    to_worker=to_worker,
                )
            )

        else:
            _obj.append(item)

    return _obj


class Plan(BaseWorker):
    """This worker does not send messages or execute any commands. Instead,
    it simply records messages that are sent to it such that message batches
    (called 'Plans') can be created and sent once."""

    def __init__(self, hook, owner, name='', *args, **kwargs):
        super().__init__(hook=hook, *args, **kwargs)

        self.name = name

        self.owner = owner
        self.location = None

        self.ptr_plan = None

        self.plan = list()
        self.readable_plan = list()
        self.arg_ids = list()
        self.result_ids = list()

    def _send_msg(self, message, location):
        return location._recv_msg(message)

    def _recv_msg(self, bin_message):
        (some_type, (msg_type, contents)) = sy.serde.deserialize(bin_message, detail=False)

        if msg_type != MSGTYPE.OBJ:
            self.plan.append(bin_message)
            self.readable_plan.append((some_type, (msg_type, contents)))

        # we can't receive the results of a plan without
        # executing it. So, execute the plan.
        if msg_type in (MSGTYPE.OBJ_REQ, MSGTYPE.IS_NONE, MSGTYPE.GET_SHAPE):
            return self.execute_plan()

        return sy.serde.serialize(None)

    def build_plan(self, args):
        print('build plan')
        # The ids of args of the first call, which should be updated when
        # the function is called with new args
        self.arg_ids = list()

        local_args = list()
        for i, arg in enumerate(args):
            self.owner.register_obj(arg)
            arg = arg.send(self)
            arg.child.garbage_collect_data = False
            self.arg_ids.append(arg.id_at_location)
            local_args.append(arg)

        res_ptr = self.plan_blueprint(*local_args)
        res_ptr.child.garbage_collect_data = False

        # The id where the result should be stored
        self.result_ids = [res_ptr.id_at_location]

    def replace_ids(self, from_ids, to_ids):
        # for every argument
        for i in range(len(from_ids)):
            # for every message
            for j, msg in enumerate(self.readable_plan):
                # look for the old id and replace it with the new one
                self.readable_plan[j] = replace_ids(
                    obj=msg,
                    change_id=from_ids[i],
                    to_id=to_ids[i],
                    from_worker=self.id,
                    to_worker=self.owner.id,
                )

    def __call__(self, *args, **kwargs):
        assert len(kwargs) == 0, 'kwargs not supported for plan'
        result_ids = [random.randint(0, 1e10)]
        return self.execute_plan(args, result_ids)

    def execute_plan(self, args, result_ids):
        first_run = self.readable_plan == []
        if first_run:
            self.build_plan(args)

        if self.location:
            if self.ptr_plan is None:
                self.readable_plan = replace_ids(
                    obj=self.readable_plan,
                    change_id=-1,
                    to_id=-1,
                    from_worker=self.owner.id,
                    to_worker=self.location.id,
                )
                self.ptr_plan = self._send(self.location)
            response = self.request_execute_plan(result_ids, *args)
            return response

        if not self.location:
            arg_ids = [arg.id for arg in args]
            self.replace_ids(self.arg_ids, arg_ids)
            self.arg_ids = arg_ids

            self.replace_ids(self.result_ids, result_ids)
            self.result_ids = result_ids

            print(self.id, self.owner.id, 'launching run')
            for message in self.readable_plan:
                bin_message = sy.serde.serialize(message, simplified=True)
                self.owner.recv_msg(bin_message)

        return sy.serde.serialize(None)

    def request_execute_plan(self, response_ids, *args):
        args = [args, response_ids]
        command = ("execute_plan", self, args)

        print('send exe cmd to ', self.location.id)
        response = self.owner.send_command(
            message=command, recipient=self.location, return_ids=response_ids
        )
        return response

    def create_pointer(
        self,
        location: BaseWorker = None,
        id_at_location: (str or int) = None,
        register: bool = False,
        owner: BaseWorker = None,
        ptr_id: (str or int) = None,
    ) -> PlanPointer:
        """Creates a pointer to the "self" torch.Tensor object.

        This method is called on a torch.Tensor object, returning a pointer
        to that object. This method is the CORRECT way to create a pointer,
        and the parameters of this method give all possible attributes that
        a pointer can be created with.

        Args:
            location: The BaseWorker object which points to the worker on which
                this pointer's object can be found. In nearly all cases, this
                is self.owner and so this attribute can usually be left blank.
                Very rarely you may know that you are about to move the Tensor
                to another worker so you can pre-initialize the location
                attribute of the pointer to some other worker, but this is a
                rare exception.
            id_at_location: A string or integer id of the tensor being pointed
                to. Similar to location, this parameter is almost always
                self.id and so you can leave this parameter to None. The only
                exception is if you happen to know that the ID is going to be
                something different than self.id, but again this is very rare
                and most of the time, setting this means that you are probably
                doing something you shouldn't.
            register: A boolean parameter (default False) that determines
                whether to register the new pointer that gets created. This is
                set to false by default because most of the time a pointer is
                initialized in this way so that it can be sent to someone else
                (i.e., "Oh you need to point to my tensor? let me create a
                pointer and send it to you" ). Thus, when a pointer gets
                created, we want to skip being registered on the local worker
                because the pointer is about to be sent elsewhere. However, if
                you are initializing a pointer you intend to keep, then it is
                probably a good idea to register it, especially if there is any
                chance that someone else will initialize a pointer to your
                pointer.
            owner: A BaseWorker parameter to specify the worker on which the
                pointer is located. It is also where the pointer is registered
                if register is set to True.
            ptr_id: A string or integer parameter to specify the id of the pointer
                in case you wish to set it manually for any special reason.
                Otherwise, it will be set randomly.
            garbage_collect_data: If true (default), delete the remote tensor when the
                pointer is deleted.

        Returns:
            A torch.Tensor[PointerTensor] pointer to self. Note that this
            object will likely be wrapped by a torch.Tensor wrapper.
        """
        if owner is None:
            owner = self.owner

        if location is None:
            location = self.owner.id

        owner = self.owner.get_worker(owner)
        location = self.owner.get_worker(location)

        if id_at_location is None:
            id_at_location = self.id

        if ptr_id is None:
            if location.id != self.owner.id:
                ptr_id = self.id
            else:
                ptr_id = int(10e10 * random.random())

        # previous_pointer = owner.get_pointer_to(location, id_at_location)
        previous_pointer = None

        if previous_pointer is None:
            ptr = PlanPointer(
                location=location,
                id_at_location=id_at_location,
                register=register,
                owner=owner,
                id=ptr_id,
            )

        return ptr

    def send(self, location):
        if self.location is not None:
            raise NotImplementedError("Can't send a Plan which already has a location")
        else:
            self.location = location
        return self

    def get(self):
        self.location = None
        self.ptr_plan = None


    def _send(self, location):
        return self.owner.send(obj=self, workers=location)

    def __str__(self):
        """Returns the string representation of PlanWorker.

        Note:
            __repr__ calls this method by default.
        """

        out = "<"
        out += str(type(self)).split("'")[1].split(".")[-1]
        out += " " + str(self.name)
        out += " id:" + str(self.id)
        out += " owner:" + str(self.owner.id)
        if self.location:
            out += " location:" + str(self.location.id)
        if len(self.readable_plan) > 0:
            out += " built"
        out += ">"
        return out
